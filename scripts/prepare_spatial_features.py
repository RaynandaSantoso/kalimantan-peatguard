"""Prepare audited annual classes and static 2020 distances for an existing grid."""
import argparse
import json

import pandas as pd

from peatguard.inventory import sha256
from peatguard.spatial import (atomic_json, dominant_classes, select_maps,
                                static_distances, validated_maps)
from peatguard.study import load_study


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root')
    parser.add_argument('--profile', choices=['legacy', 'expanded'], default='expanded')
    parser.add_argument('--start')
    parser.add_argument('--end', default='latest')
    parser.add_argument('--as-of')
    parser.add_argument('--policy', choices=['strict_same_year', 'carry_forward'], default='strict_same_year')
    parser.add_argument('--include-held-out', action='store_true')
    args = parser.parse_args()
    study = load_study(root=args.root, profile=args.profile, start=args.start, end=args.end, as_of=args.as_of)
    grid_dir = study.root / 'data/interim/study_grid_5km'
    manifest = json.loads((grid_dir / 'manifest.json').read_text())
    expected = study.config['grid']
    if (manifest['cell_size_m'] != expected['cell_size_m']
            or manifest['grid_crs_proj'] != expected['crs_proj']
            or manifest['lattice_origin_m'] != expected['lattice_origin_m']
            or manifest['big_mask_rule'] != 'all downloaded BIG polygons, dissolved and intersected with province union'):
        raise ValueError('Candidate grid/mask differs from the study configuration.')
    for source in manifest['sources']:
        if sha256(study.root / source['path']) != source['sha256']:
            raise ValueError('Study boundary changed; rebuild/review the candidate grid first.')
    cells = pd.read_csv(grid_dir / 'study_grid_5km.csv')
    if cells['intersects_big_peatland'].dtype != bool:
        raise ValueError('Expected explicit boolean candidate-mask flags.')
    cells = cells[cells.intersects_big_peatland].copy()
    assignments = select_maps(study, policy=args.policy, include_held_out=args.include_held_out)
    # Report blockers before starting expensive distance computation.
    print(json.dumps(assignments, indent=2))
    if any(row['status'] != 'ready' for row in assignments):
        raise SystemExit('Missing same-year maps block feature preparation. Explicit carry_forward is an alternative research policy.')
    map_years = {row['map_year'] for row in assignments}
    maps = {year: path for year, path in validated_maps(study).items() if year in map_years}
    classes = dominant_classes(study, cells, maps)
    distances = static_distances(study, cells)
    # A committed manifest identifies the build; incomplete directories are never valid builds.
    output = study.output_directory / ('spatial_' + args.policy + ('_heldout' if args.include_held_out else '_development'))
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for name, frame in [('modis_classes.csv', classes), ('ghsl_distance_2020.csv', distances)]:
        temporary = output / ('.' + name + '.tmp')
        frame.to_csv(temporary, index=False)
        temporary.replace(output / name)
        artifacts[name] = sha256(output / name)
    atomic_json(output / 'manifest.json', {'study': study.metadata(), 'assignments': assignments,
                'candidate_grid_sha256': sha256(grid_dir / 'study_grid_5km.csv'),
                'map_hashes': {str(y): sha256(p) for y, p in maps.items()},
                'ghsl_reference_epoch': 2020, 'artifacts': artifacts,
                'scientific_status': 'candidate_pending_review'})
    print(output)


if __name__ == '__main__':
    main()
