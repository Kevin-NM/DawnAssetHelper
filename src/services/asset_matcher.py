import os
import shutil
from pathlib import Path
from src.services.logger_service import logger
from src.utils.spine_utils import parse_spine_atlas, AtlasPage

def get_canonical_output_filename(hero_id: str, asset_kind: str, source_ext: str = "") -> str:
    if asset_kind == "skeleton":
        ext = source_ext if source_ext in (".skel", ".json") else ".skel"
        return f"{hero_id}{ext}"
    if asset_kind == "atlas":
        return f"{hero_id}.atlas"
    if asset_kind == "texture":
        return f"{hero_id}.png"
    return ""

def strip_unity_skel_header(src: Path, dest: Path) -> bool:
    """Strip Unity header from .skel.asset and save as raw .skel binary."""
    try:
        with open(src, 'rb') as f:
            data = f.read()
        if len(data) < 30:
            return False
        hash_len = data[0]
        offset = 1 + hash_len
        if offset >= len(data):
            return False
        # Verify the data at offset looks like a valid Spine binary
        # (version string should start with "3.8")
        ver_len = data[offset]
        if ver_len >= 128 or ver_len < 4 or ver_len > 20:
            return False
        ver = data[offset+1:offset+1+ver_len]
        if not ver.startswith(b'3.'):
            return False
        with open(dest, 'wb') as f:
            f.write(data[offset:])
        return True
    except Exception as e:
        logger.error(f"Failed to strip Unity header from {src.name}: {e}")
        return False


def is_atlas_text(file_path: Path) -> bool:
    if not file_path.exists(): return False
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read(4096).lower()
            keywords = ["size:", "format:", "filter:", "repeat:", "rotate:", "xy:", "orig:", "offset:", "index:"]
            matches = sum(1 for kw in keywords if kw in content)
            return matches >= 2
    except Exception:
        return False

def is_skeleton_json(file_path: Path) -> bool:
    if not file_path.exists(): return False
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read(4096).lower()
            keywords = ["skeleton", "bones", "slots", "skins", "animations"]
            matches = sum(1 for kw in keywords if kw in content)
            return matches >= 2
    except Exception:
        return False

def is_png(file_path: Path) -> bool:
    if not file_path.exists(): return False
    try:
        with open(file_path, 'rb') as f:
            header = f.read(8)
            return header == b'\x89\x50\x4e\x47\x0d\x0a\x1a\x0a'
    except Exception:
        return False

def classify_candidate_file(hero_id: str, file_path: Path) -> str | None:
    lower_name = file_path.name.lower()
    
    # Texture priority check
    if file_path.suffix.lower() == ".png" or is_png(file_path):
        return "texture"
        
    # Atlas priority check
    if f"{hero_id.lower()}.atlas" in lower_name or "_atlas" in lower_name or ".atlas" in lower_name:
        return "atlas"
    if file_path.suffix.lower() in [".asset", ".txt"] and is_atlas_text(file_path):
        return "atlas"
        
    # Skeleton check
    if lower_name.startswith(hero_id.lower()) and lower_name.endswith((".skel", ".skel.bytes", ".skel.asset", ".json")):
        return "skeleton"
    if "skeletondata" in lower_name:
        return "skeleton"
    if file_path.suffix.lower() in [".asset", ".txt", ".json"] and is_skeleton_json(file_path):
        return "skeleton"
        
    return None

class AssetMatcher:
    def __init__(self, raw_dir: Path, matched_dir: Path, hero_id: str):
        self.raw_dir = raw_dir
        self.matched_dir = matched_dir
        self.hero_id = hero_id
        
        self.matched_dir.mkdir(parents=True, exist_ok=True)
        # Clear existing files in matched_dir
        for item in self.matched_dir.iterdir():
            if item.is_file():
                item.unlink()
                
        self.all_files = [p for p in self.raw_dir.rglob("*") if p.is_file()]

    def match_all(self) -> tuple[dict|None, dict|None, dict|None]:
        skeleton_cands = []
        atlas_cands = []
        texture_cands = []
        
        for p in self.all_files:
            kind = classify_candidate_file(self.hero_id, p)
            if kind == "skeleton":
                skeleton_cands.append(p)
            elif kind == "atlas":
                atlas_cands.append(p)
            elif kind == "texture":
                texture_cands.append(p)
                
        # 1. Pick Skeleton (primary + variants)
        skel_src = self.pick_best_skeleton(skeleton_cands)
        skel_dict = self._copy_and_return(skel_src, "skeleton") if skel_src else None
        
        # Copy variant subfolders (e.g. matched/hero1201_01/ with its own skel+atlas+png)
        self._copy_variant_subfolders(skeleton_cands, atlas_cands, texture_cands, skel_src)
        
        # 2. Pick Atlas+Texture Pair
        best_pair = self.pick_best_atlas_texture_pair(atlas_cands, texture_cands)
        
        atlas_dict = None
        tex_dict = None
        
        if best_pair:
            atlas_src = best_pair["atlas"]
            atlas_dict = self._copy_and_return(atlas_src, "atlas")
            
            # We might have multiple textures for multiple pages
            tex_dicts = []
            all_matched = True
            for page_info in best_pair["pages"]:
                tex_src = page_info.get("selected_texture")
                if tex_src:
                    # We copy with page_name
                    t_dict = self._copy_texture_with_name(tex_src, page_info["page_name"])
                    if t_dict:
                        tex_dicts.append(t_dict)
                    else:
                        all_matched = False
                else:
                    all_matched = False
            
            if tex_dicts:
                # For backward compat, we use the first one as primary
                tex_dict = tex_dicts[0]
                tex_dict["all_textures"] = tex_dicts
            
            if not all_matched:
                if tex_dict:
                    tex_dict["status"] = "partial_success"
                logger.warning(f"[Matcher] Some textures were not found for {self.hero_id} atlas pages.")
            
            # Save diagnostics
            self._save_diagnostic(best_pair)
        
        found_count = sum(1 for x in [skel_dict, atlas_dict, tex_dict] if x is not None)
        logger.info(f"[Matcher] Hero {self.hero_id} matched {found_count}/3 required files.")
        
        if not skel_dict: logger.error(f"Missing skeleton for {self.hero_id}")
        if not atlas_dict: logger.error(f"Missing atlas for {self.hero_id}")
        if not tex_dict: logger.error(f"Missing texture for {self.hero_id}")
        
        return skel_dict, atlas_dict, tex_dict

    def pick_best_atlas_texture_pair(self, atlas_cands: list[Path], texture_cands: list[Path]) -> dict | None:
        if not atlas_cands: return None
        
        pairs = []
        for atlas in atlas_cands:
            atlas_data = parse_spine_atlas(atlas)
            pages = atlas_data["pages"]
            if not pages: continue
            
            page_matches = []
            total_pair_score = 0
            
            # Score atlas itself
            if f"{self.hero_id.lower()}.atlas" in atlas.name.lower():
                total_pair_score += 100
            
            for page in pages:
                best_tex, score = self.find_texture_for_atlas_page(page, texture_cands, atlas)
                page_matches.append({
                    "page_name": page.page_name,
                    "atlas_page_size": [page.width, page.height],
                    "selected_texture": best_tex,
                    "texture_size": None,
                    "score": score,
                    "matched": best_tex is not None
                })
                total_pair_score += score
                
                if best_tex:
                    from PIL import Image
                    try:
                        with Image.open(best_tex) as img:
                            page_matches[-1]["texture_size"] = list(img.size)
                    except:
                        pass
            
            pairs.append({
                "atlas": atlas,
                "pages": page_matches,
                "score": total_pair_score,
                "matched_pages": sum(1 for p in page_matches if p["matched"])
            })
            
        if not pairs: return None
        
        # Sort by score, then by matched_pages
        pairs.sort(key=lambda x: (x["score"], x["matched_pages"]), reverse=True)
        best = pairs[0]
        
        if len(pairs) > 1:
            logger.info(f"[Matcher] Found {len(pairs)} atlas pairs. Selected best score: {best['score']}")
            
        return best

    def find_texture_for_atlas_page(self, page: AtlasPage, texture_candidates: list[Path], atlas_src: Path) -> tuple[Path | None, int]:
        best_tex = None
        max_score = -1
        
        from PIL import Image
        
        for tex in texture_candidates:
            score = 0
            # 1. Page name match
            if tex.name.lower() == page.page_name.lower():
                score += 100
            elif page.page_name.lower() in tex.name.lower():
                score += 50
                
            # 2. Same bundle/dir
            if tex.parent == atlas_src.parent:
                score += 50
            elif tex.parts[-3:-1] == atlas_src.parts[-3:-1]: # likely same attempt
                score += 20
                
            # 3. Size match (CRITICAL)
            try:
                with Image.open(tex) as img:
                    if img.size == (page.width, page.height):
                        score += 100
                    else:
                        # Major penalty for dimension mismatch
                        score -= 500
            except:
                pass
                
            if score > max_score:
                max_score = score
                best_tex = tex
                
        # If best score is too low or negative (likely dimension mismatch), we might reject
        if max_score < 0:
            return None, max_score
            
        return best_tex, max_score

    def _copy_texture_with_name(self, src_path: Path, target_name: str) -> dict | None:
        # Ensure target_name has .png
        if not target_name.lower().endswith(".png"):
            target_name += ".png"

        safe_parts = [p for p in target_name.replace("\\", "/").split("/") if p and p not in [".", ".."]]
        safe_target_name = "/".join(safe_parts) if safe_parts else src_path.name
        dest_path = self.matched_dir.joinpath(*safe_target_name.split("/"))
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest_path)

            # Keep a canonical texture beside the atlas for older flows that expect <hero_id>.png.
            canonical_path = self.matched_dir / f"{self.hero_id}.png"
            if not canonical_path.exists():
                shutil.copy2(src_path, canonical_path)

            rel_src = src_path.as_posix()
            if "raw_export" in rel_src:
                rel_src = "raw_export" + rel_src.split("raw_export")[1]
            output_rel = dest_path.relative_to(self.matched_dir).as_posix()
            return {
                "source": rel_src,
                "output": f"matched/{output_rel}",
                "page_name": safe_target_name,
                "canonical_output": f"matched/{self.hero_id}.png"
            }
        except Exception as e:
            logger.error(f"Failed to copy texture {src_path.name} to {safe_target_name}: {e}")
            return None

    def _save_diagnostic(self, pair_data: dict):
        diag_dir = self.matched_dir.parent / "diagnostics"
        diag_dir.mkdir(parents=True, exist_ok=True)
        
        diag = {
            "hero_id": self.hero_id,
            "selected_atlas": pair_data["atlas"].as_posix(),
            "atlas_pages": [],
            "errors": []
        }
        
        for p in pair_data["pages"]:
            diag["atlas_pages"].append({
                "page_name": p["page_name"],
                "atlas_page_size": p["atlas_page_size"],
                "selected_texture": p["selected_texture"].as_posix() if p["selected_texture"] else None,
                "texture_size": p["texture_size"],
                "matched": p["matched"],
                "score": p["score"]
            })
            if not p["matched"]:
                diag["errors"].append(f"No matching texture found for page {p['page_name']}")
            elif p["atlas_page_size"] != p["texture_size"]:
                diag["errors"].append(f"Dimension mismatch for page {p['page_name']}: Atlas {p['atlas_page_size']} vs Texture {p['texture_size']}")
                
        import json
        with open(diag_dir / "atlas_texture_pair.json", 'w', encoding='utf-8') as f:
            json.dump(diag, f, indent=2, ensure_ascii=False)

    def pick_best_skeleton(self, candidates: list[Path]) -> Path | None:
        if not candidates: return None
            
        def score(p: Path):
            name = p.name.lower()
            if name.endswith((".skel.bytes", ".skel.asset")): return 6
            if name.endswith(".skel"): return 5
            if f"{self.hero_id.lower()}.json" in name: return 3
            if "skeletondata" in name: return 2
            return 0
            
        best = max(candidates, key=lambda p: (score(p), p.stat().st_size if p.exists() else 0))
        if len(candidates) > 1:
            logger.warning(f"Multiple skeleton candidates found for {self.hero_id}. Selected {best.name}.")
        return best

    def pick_best_atlas(self, candidates: list[Path]) -> Path | None:
        if not candidates: return None
            
        def score(p: Path):
            name = p.name.lower()
            if f"{self.hero_id.lower()}.atlas" in name: return 4
            if "_atlas" in name: return 3
            if ".atlas" in name: return 2
            return 1
            
        best = max(candidates, key=lambda p: (score(p), p.stat().st_size if p.exists() else 0))
        if len(candidates) > 1:
            logger.warning(f"Multiple atlas candidates found for {self.hero_id}. Selected {best.name}.")
        return best

    def pick_best_texture(self, candidates: list[Path]) -> Path | None:
        if not candidates: return None
            
        def score(p: Path):
            name = p.name.lower()
            if f"{self.hero_id.lower()}.png" == name: return 4
            if f"{self.hero_id.lower()}.png" in name: return 3
            if is_png(p): return 2
            return 1
            
        best = max(candidates, key=lambda p: (score(p), p.stat().st_size if p.exists() else 0))
        if len(candidates) > 1:
            logger.warning(f"Multiple texture candidates found for {self.hero_id}. Selected {best.name}.")
        return best

    def _copy_and_return(self, src_path: Path, asset_kind: str) -> dict | None:
        target_name = get_canonical_output_filename(self.hero_id, asset_kind, src_path.suffix.lower().replace('.asset', ''))
        dest_path = self.matched_dir / target_name
        
        try:
            shutil.copy2(src_path, dest_path)
        except Exception as e:
            logger.error(f"Failed to copy {src_path} to {dest_path}: {e}")
            return None
            
        rel_src = src_path.as_posix()
        if "raw_export" in rel_src:
            rel_src = "raw_export" + rel_src.split("raw_export")[1]
            
        output_rel = f"matched/{dest_path.name}"
        logger.info(f"[Matcher] Selected {asset_kind}:\nsource: {rel_src}\noutput: {output_rel}")
        return {
            "source": rel_src,
            "output": output_rel
        }

    def _copy_variant_subfolders(self, skel_cands: list[Path], atlas_cands: list[Path], tex_cands: list[Path], primary: Path | None):
        """For multi-skel heroes, create subfolders per variant with properly named files."""
        import re
        hero_lower = self.hero_id.lower()
        variant_map = {}

        def _skel_sort_key(src: Path):
            n = src.name.lower()
            if n.endswith((".skel.asset", ".skel.bytes")): return 0
            if n.endswith(".skel"): return 1
            if "skeletondata" in n: return 2
            return 3

        for src in sorted(skel_cands, key=_skel_sort_key):
            name_lower = src.name.lower()
            if not (name_lower.endswith((".skel", ".skel.bytes", ".skel.asset")) or ("skeletondata" in name_lower and name_lower.endswith(".json"))):
                continue
            m = re.search(rf'^{re.escape(self.hero_id)}_(\d+)', src.name)
            if not m:
                continue
            vid = m.group(1)
            if vid in variant_map and "skel" in variant_map[vid]:
                continue
            variant_map.setdefault(vid, {})["skel"] = src

        for src in atlas_cands:
            m = re.search(rf'^{re.escape(self.hero_id)}_(\d+)\.atlas\.asset$', src.name, re.IGNORECASE)
            if m:
                variant_map.setdefault(m.group(1), {})["atlas"] = src

        for src in tex_cands:
            m = re.search(rf'^{re.escape(self.hero_id)}_(\d+)\.png$', src.name, re.IGNORECASE)
            if m:
                variant_map.setdefault(m.group(1), {})["tex"] = src

        for vid, files in sorted(variant_map.items()):
            if "skel" not in files:
                logger.warning(f"[Matcher] Variant {vid}: no skeleton found, skipping")
                continue
            subdir = self.matched_dir / f"{self.hero_id}_{vid}"
            subdir.mkdir(parents=True, exist_ok=True)
            logger.info(f"[Matcher] Creating variant subfolder: {subdir.name}/")

            skel_src = files["skel"]
            skel_dest = subdir / f"{self.hero_id}_{vid}.skel"
            shutil.copy2(skel_src, skel_dest)
            logger.info(f"[Matcher] Variant {vid}: copied skeleton {skel_src.name}")

            if "atlas" in files:
                shutil.copy2(files["atlas"], subdir / f"{self.hero_id}_{vid}.atlas")
                logger.info(f"[Matcher] Variant {vid}: copied atlas {files['atlas'].name}")

            if "tex" in files:
                shutil.copy2(files["tex"], subdir / f"{self.hero_id}_{vid}.png")
                logger.info(f"[Matcher] Variant {vid}: copied texture {files['tex'].name}")
