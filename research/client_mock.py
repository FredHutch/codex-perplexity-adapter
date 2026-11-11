"""Installed/current clients against an isolated experimental mock provider."""
import argparse
import json
from pathlib import Path
from unittest.mock import patch
from research.tool_continuation import experiment
from scripts import qualify_clients as q
from research.budget import OUT

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--strategy',choices=['replay','direct','anchor'],required=True);p.add_argument('--client',required=True);a=p.parse_args()
    target=OUT/f'client-mock-{a.strategy}';target.mkdir(exist_ok=True,parents=True)
    with experiment(a.strategy),patch.object(q,'DIRECTORY',target):
        report=q.run(a.client,False)
        assert report['returncode']==0 and report['completed_final_answer'] and report['tool_result_received'] and report['shell_command_completed'], report
