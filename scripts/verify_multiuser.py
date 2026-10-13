"""Sequential stage gates. All includes every V1/V2 test, both browsers and stability."""
import argparse
import os
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STAGES={
 'P0':['test_baseline.py'], 'P1':['test_schema.py'], 'P2':['test_auth.py'],
 'P3':['test_documents_isolation.py','test_retrieval_isolation.py'], 'P4':['test_chat_isolation.py'],
 'P5':['test_models.py','test_outbound.py'], 'P6':['test_quotas.py','test_workers.py'],
 'P7':[], 'P8':['test_admin.py'], 'P9':['test_exports.py','test_migration.py'],
 'P10':['test_deployment.py'], 'P11':[]}


def run(args,cwd=ROOT,env=None):
    result=subprocess.run(args,cwd=cwd,env=env)
    if result.returncode:raise SystemExit(result.returncode)


def main():
    parser=argparse.ArgumentParser();choice=parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--stage',choices=STAGES);choice.add_argument('--all',action='store_true');choice.add_argument('--benchmark',action='store_true')
    args=parser.parse_args();run([sys.executable,str(ROOT/'scripts/prepare-multiuser-test.py')])
    if args.benchmark:
        run([sys.executable,str(ROOT/'tests/multiuser/benchmark_multiuser.py')]);return
    stage=args.stage or 'P11';folder=ROOT/'.reports/multiuser'/stage;folder.mkdir(parents=True,exist_ok=True)
    if args.all:targets=['tests']
    else:targets=['tests/multiuser/'+file for key,files in STAGES.items() if int(key[1:])<=int(stage[1:]) for file in files]
    run([sys.executable,'-m','pytest',*targets,'--junitxml='+str(folder/'verification.xml')])
    if int(stage[1:])>=7:
        npm='npm.cmd' if os.name=='nt' else 'npm';npx='npx.cmd' if os.name=='nt' else 'npx'
        env=dict(os.environ);env.pop('VITE_MULTIUSER',None)
        run([npm,'run','build'],ROOT/'frontend',env)
        run([npx,'playwright','test'],ROOT/'frontend',env)
        env['VITE_MULTIUSER']='true'
        run([npm,'run','build'],ROOT/'frontend',env)
        run([npx,'playwright','test','--config','playwright.multiuser.config.ts'],ROOT/'frontend',env)
        if args.all:
            env['MULTIUSER_BROWSER']='chromium'
            run([npx,'playwright','test','--config','playwright.multiuser.config.ts'],ROOT/'frontend',env)
    if args.all:
        run([sys.executable,str(ROOT/'tests/multiuser/benchmark_multiuser.py')])
        run([sys.executable,str(ROOT/'tests/multiuser/stability_multiuser.py'),'--duration-seconds','1800'])
    print('Selected local gates passed. Real external API/public domain require separately recorded credentials/environment.')


if __name__=='__main__':main()
