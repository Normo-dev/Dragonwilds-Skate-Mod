"""Retire acknowledged capture reports outside the Unreal game thread."""
from pathlib import Path
import json,re

def cleanup_reports(mailbox,limit=8):
    root=Path(mailbox).resolve();removed=0
    def plain(name,pattern):
        if not isinstance(name,str)or re.fullmatch(pattern,name)is None:raise ValueError('Invalid cleanup basename')
        path=root/name
        if path.is_symlink()or path.resolve()!=path or(hasattr(path,'is_junction')and path.is_junction()):raise ValueError('Linked cleanup file')
        return path
    for marker in sorted(root.glob('building-cleanup-*.json'))[:limit]:
        try:
            marker=plain(marker.name,r'building-cleanup-[0-9]+-[0-9]+-[0-9]+\.json')
            if marker.stat().st_size>4096:continue
            receipt=json.loads(marker.read_bytes());ident=receipt.get('capture_id')
            if receipt.get('schema')!='S3BUILDINGCLEANUP1'or not isinstance(ident,str)or marker.name!='building-cleanup-'+ident+'.json':continue
            report=plain(receipt.get('report'),r'building-state2-[0-9]+-[0-9]+-[0-9]+\.json')
            if report.name!='building-state2-'+ident+'.json'or report.stat().st_size>33554432:continue
            state=json.loads(report.read_bytes())
            if state.get('capture_id')!=ident or state.get('schema')!='S3BUILDINGSTATE2':continue
            packet_name=receipt.get('packet');captured=(state.get('piece_packets')or{}).get('path')
            if captured!=packet_name:continue
            packet=plain(packet_name,r'building-packets-[0-9]+-[0-9]+-[0-9]+\.bin')if packet_name is not None else None
            if packet and packet.exists()and packet.stat().st_size!=(state['piece_packets'].get('bytes')):continue
            # All targets are individually validated mailbox basenames. Never
            # recurse, replace or touch saves/private prepared collision data.
            if packet and packet.exists():packet.unlink()
            report.unlink();marker.unlink();removed+=1
        except (OSError,ValueError,TypeError,KeyError):continue
    return removed
