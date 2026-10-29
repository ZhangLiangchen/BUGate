import sys, xml.etree.ElementTree as E
from pathlib import Path
def bounded(value): return value if len(sys.argv)>2 and sys.argv[2]=='broken' else max(0,value)
root=E.Element('testsuite')
failed=0
for name,value,expected in [('test_positive',3,3),('test_negative',-1,0)]:
    case=E.SubElement(root,'testcase',classname='fixture',name=name)
    try: assert bounded(value)==expected, 'wrong business result'
    except AssertionError as exc:
        failed+=1;E.SubElement(case,'failure',message=str(exc))
E.ElementTree(root).write(sys.argv[1],encoding='utf-8')
print('executed',len(root),'cases')
sys.exit(1 if failed else 0)
