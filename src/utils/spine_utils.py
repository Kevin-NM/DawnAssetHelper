import re
from pathlib import Path
from src.services.logger_service import logger

class AtlasPage:
    def __init__(self, page_name: str, width: int, height: int):
        self.page_name = page_name
        self.width = width
        self.height = height

    def __repr__(self):
        return f"AtlasPage({self.page_name}, {self.width}x{self.height})"

class AtlasRegion:
    def __init__(self, name: str, page: str, rotate: bool, xy: tuple, size: tuple, orig: tuple, offset: tuple, index: int):
        self.name = name
        self.page = page
        self.rotate = rotate
        self.xy = xy
        self.size = size
        self.orig = orig
        self.offset = offset
        self.index = index

def parse_spine_atlas(atlas_path: Path) -> dict:
    """
    Parses a Spine atlas file and returns a dictionary with 'pages' and 'regions'.
    """
    result = {"pages": [], "regions": []}
    if not atlas_path.exists():
        return result

    try:
        with open(atlas_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        current_page = None
        current_region = None
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            
            # Check if this is a page line (no colon, followed by size:)
            if ":" not in line and i + 1 < len(lines) and "size:" in lines[i+1].lower():
                current_page = line
                # Parse page details
                i += 1
                w, h = 0, 0
                while i < len(lines) and ":" in lines[i]:
                    detail = lines[i].strip().lower()
                    if detail.startswith("size:"):
                        parts = detail.split(":")[1].split(",")
                        w, h = int(parts[0].strip()), int(parts[1].strip())
                    i += 1
                result["pages"].append(AtlasPage(current_page, w, h))
                continue
            
            # Check if this is a region line (followed by rotate, xy, etc.)
            if ":" not in line and i + 1 < len(lines) and "rotate:" in lines[i+1].lower():
                region_name = line
                i += 1
                rotate = False
                xy = (0, 0)
                size = (0, 0)
                orig = (0, 0)
                offset = (0, 0)
                index = -1
                
                while i < len(lines) and ":" in lines[i]:
                    detail = lines[i].strip().lower()
                    if detail.startswith("rotate:"):
                        rotate = "true" in detail
                    elif detail.startswith("xy:"):
                        parts = detail.split(":")[1].split(",")
                        xy = (int(parts[0].strip()), int(parts[1].strip()))
                    elif detail.startswith("size:"):
                        parts = detail.split(":")[1].split(",")
                        size = (int(parts[0].strip()), int(parts[1].strip()))
                    elif detail.startswith("orig:"):
                        parts = detail.split(":")[1].split(",")
                        orig = (int(parts[0].strip()), int(parts[1].strip()))
                    elif detail.startswith("offset:"):
                        parts = detail.split(":")[1].split(",")
                        offset = (int(parts[0].strip()), int(parts[1].strip()))
                    elif detail.startswith("index:"):
                        index = int(detail.split(":")[1].strip())
                    i += 1
                
                # If no orig provided, use size
                if orig == (0, 0):
                    orig = size
                    
                result["regions"].append(AtlasRegion(region_name, current_page, rotate, xy, size, orig, offset, index))
                continue
            
            i += 1
            
    except Exception as e:
        logger.error(f"Error parsing atlas {atlas_path.name}: {e}")

    return result
