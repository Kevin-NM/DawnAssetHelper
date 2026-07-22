import re
from pathlib import Path
from src.models import HeroBundleGroup

def parse_hero_number(hero_id: str) -> int | None:
    match = re.search(r'hero(\d+)', hero_id)
    if match:
        return int(match.group(1))
    return None

def classify_hero_type(hero_id: str) -> str:
    num = parse_hero_number(hero_id)
    if num is None:
        return "Unknown"
    
    if num > 10000:
        return "Skin"
    if num > 1200:
        return "Collab"
    return "Original"

class HeroScanner:
    def __init__(self, bundle_folder: str):
        self.bundle_folder = Path(bundle_folder)

    def scan(self) -> list[HeroBundleGroup]:
        if not self.bundle_folder.exists() or not self.bundle_folder.is_dir():
            return []

        pattern = re.compile(r'^roles_showroles_spine_(hero\d+)(?:_(hero\d+[a-zA-Z]?))?_depend_\d+\.ab$')
        
        groups_map = {}
        for file_path in self.bundle_folder.iterdir():
            if not file_path.is_file() or not file_path.name.endswith('.ab'):
                continue
            
            match = pattern.match(file_path.name)
            if match:
                main_id = match.group(1)
                variant_id = match.group(2)
                
                if main_id not in groups_map:
                    groups_map[main_id] = {
                        'hero_id': main_id,
                        'hero_number': parse_hero_number(main_id),
                        'hero_type': classify_hero_type(main_id),
                        'variant_ids': set(),
                        'bundle_files': [],
                        'total_size': 0
                    }
                
                if variant_id:
                    groups_map[main_id]['variant_ids'].add(variant_id)
                groups_map[main_id]['bundle_files'].append(file_path)
                groups_map[main_id]['total_size'] += file_path.stat().st_size

        results = []
        for main_id, data in groups_map.items():
            file_count = len(data['bundle_files'])
            status = "Ready" if file_count >= 2 else "Partial"
            
            group = HeroBundleGroup(
                hero_id=data['hero_id'],
                hero_number=data['hero_number'],
                hero_type=data['hero_type'],
                variant_ids=list(data['variant_ids']),
                bundle_files=data['bundle_files'],
                file_count=file_count,
                total_size=data['total_size'],
                status=status
            )
            results.append(group)
            
        return sorted(results, key=lambda x: x.hero_number if x.hero_number else 0)
