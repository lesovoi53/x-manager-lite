"""Edition boundary and opt-in TCP transaction; all filesystem/sysctl are fake."""
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
def load(name):
 s=importlib.util.spec_from_file_location(name,ROOT/'scripts'/(name+'.py'))
 m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
tcp=load('tcp-profile');edition=load('lite-edition')

class LiteContract(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
  self.calls=[];self.fail=None
  self.tool=tcp.Profile(self.root,run=self.run_command);self.tool.lock=nullcontext
  self.before=dict(zip(tcp.KEYS,['4294967295','1','1024']))
  for k,v in self.before.items():self.write('/proc/sys/'+k.replace('.','/'),v)
 def write(self,name,value):
  p=self.root/name.lstrip('/');p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value)
 def run_command(self,*args):
  self.calls.append(args);self.assertEqual(args[:2],('sysctl','-w'))
  k,v=args[2].split('=',1)
  if k==self.fail:self.fail=None;raise RuntimeError('injected sysctl failure')
  self.write('/proc/sys/'+k.replace('.','/'),v)
 def test_preview_and_selected_apply_rollback(self):
  plan=self.tool.plan(selected=['net.ipv4.tcp_fastopen'])
  self.assertEqual(plan['desired'],{'net.ipv4.tcp_fastopen':'1027'})
  self.assertFalse(self.calls)
  self.tool.apply_selected(['net.ipv4.tcp_fastopen'])
  self.assertEqual(self.tool.effective(),self.before|{'net.ipv4.tcp_fastopen':'1027'})
  self.tool.rollback();self.assertEqual(self.tool.effective(),self.before)
  self.assertFalse(self.tool.path(tcp.base.CONFIG).exists())
 def test_partial_failure_restores_every_value(self):
  self.fail='net.ipv4.tcp_slow_start_after_idle'
  with self.assertRaises(RuntimeError):self.tool.apply_selected(list(tcp.KEYS))
  self.assertEqual(self.tool.effective(),self.before)
  self.assertFalse(self.tool.path(tcp.base.STATE).exists())
 def test_retry_and_admin_changes_never_overwritten(self):
  self.tool.apply_selected(list(tcp.KEYS))
  with self.assertRaises(ValueError):self.tool.apply_selected(list(tcp.KEYS))
  self.write('/proc/sys/net/ipv4/tcp_notsent_lowat','20000')
  with self.assertRaises(ValueError):self.tool.rollback()
  self.assertEqual(self.tool.effective()['net.ipv4.tcp_notsent_lowat'],'20000')
 def test_conflicting_config_blocks_before_mutation(self):
  self.write('/etc/sysctl.d/99-custom.conf','net.ipv4.tcp_notsent_lowat=12345\n')
  with self.assertRaises(ValueError):self.tool.apply_selected(list(tcp.KEYS))
  self.assertFalse(self.calls)
 def test_edition_boundary(self):
  edition.check(self.root)
  self.write('/usr/local/bin/x-manager','existing')
  with self.assertRaises(ValueError):edition.check(self.root)
  self.write('/usr/local/share/x-manager/components.json',json.dumps({'repository':'lesovoi53/xray-manager'}))
  with self.assertRaises(ValueError):edition.check(self.root)
  self.write('/usr/local/share/x-manager/components.json',json.dumps({'repository':edition.REPOSITORY,'edition':'lite'}))
  edition.check(self.root)
 def test_release_components_and_service_scope(self):
  manifest=json.loads((ROOT/'components.json').read_text())
  self.assertEqual(manifest['edition'],'lite')
  self.assertEqual(manifest['repository'],edition.REPOSITORY)
  self.assertFalse(any(x in asset for asset in manifest['sha256'] for x in ('openflux','webdav','volga','quic-proxy')))
  units=load('tuna-watchdog').UNITS
  self.assertTrue({'snell','mita','masterdns','wdtt','csqtt','tuna-subscriptions'}<=set(units))
  self.assertFalse(any(u.startswith(('openflux','webdav','cotten','volga')) for u in units))

if __name__=='__main__':unittest.main()
