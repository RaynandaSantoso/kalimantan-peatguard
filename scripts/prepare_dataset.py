"""Prepare versioned same-day research tables from validated local sources."""
import argparse
import json

from peatguard.datasets import prepare,CoverageError
from peatguard.study import load_study


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root')
    parser.add_argument('--profile',choices=['expanded','legacy'],default='expanded')
    parser.add_argument('--start')
    parser.add_argument('--end',default='latest')
    parser.add_argument('--as-of',required=True)
    parser.add_argument('--ledger',required=True,help='Checksummed FIRMS interval ledger with export/query evidence')
    parser.add_argument('--map-policy',choices=['strict_same_year','carry_forward'],default='strict_same_year')
    parser.add_argument('--coverage-policy',choices=['require_complete','exclude_ineligible'],default='require_complete')
    parser.add_argument('--held-out-start')
    parser.add_argument('--held-out-end')
    args=parser.parse_args()
    if bool(args.held_out_start)!=bool(args.held_out_end):parser.error('Both held-out boundaries are required.')
    try:
        study=load_study(root=args.root,profile=args.profile,start=args.start,end=args.end,as_of=args.as_of)
        result=prepare(study,args.ledger,map_policy=args.map_policy,coverage_policy=args.coverage_policy,
            held_out_interval=(args.held_out_start,args.held_out_end) if args.held_out_start else None)
    except (ValueError,OSError,KeyError) as error:
        parser.exit(1,f'Dataset preparation blocked: {error}\n')
    print(json.dumps({'build_directory':str(result),'build_id':result.name,'model_training_performed':False},indent=2))


if __name__=='__main__':main()
