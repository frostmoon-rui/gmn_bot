"""Bounded maintenance for generated files and expired caches, never user records."""
import asyncio
import fnmatch
import gc
import math
import os
from pathlib import Path
import time


DAY=86400
# Only these generated-data directories may be cleaned. Backups are excluded.
POLICIES=(('logs/startup',('*.log',),14*DAY,64*1024**2),
          ('weather_diagnostics',('*.json','*.txt','*.png'),3*DAY,32*1024**2),
          ('screenshots',('*.png','*.jpg'),7*DAY,64*1024**2))


def linked(path):
    return path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction())


def safe_file(path, root):
    if not path.resolve().is_relative_to(root):
        return False
    current=path
    while current!=root:
        if linked(current):
            return False
        current=current.parent
    return path.is_file()


def cleanup_generated(root, *, dry_run=True, now=None):
    root=Path(root).resolve(strict=True)
    now=time.time() if now is None else now
    report={'files':0,'bytes':0,'errors':0,'dry_run':dry_run}
    for relative,patterns,retention,budget in POLICIES:
        directory=root/relative
        if not directory.is_dir() or linked(directory) or not directory.resolve().is_relative_to(root):
            continue
        if any(linked(parent) for parent in directory.parents if parent!=root and parent.is_relative_to(root)):
            continue
        files=[]
        for folder,dirs,names in os.walk(directory,followlinks=False):
            dirs[:]=[name for name in dirs if not linked(Path(folder)/name)]
            for name in names:
                path=Path(folder)/name
                try:
                    if not any(fnmatch.fnmatch(name,pattern) for pattern in patterns) or not safe_file(path,root):
                        continue
                    info=path.stat()
                    files.append((info.st_mtime,info.st_size,path))
                except OSError:
                    report['errors']+=1
        total=sum(size for _,size,_ in files)
        for modified,size,path in sorted(files):
            # A recent file may still be open by the bot, supervisor or browser.
            if now-modified<DAY or (now-modified<=retention and total<=budget):
                continue
            if report['files']>=200:
                return report
            try:
                current=path.stat()
                if not safe_file(path,root) or current.st_mtime!=modified or current.st_size!=size:
                    continue
                if not dry_run:
                    path.unlink()
                total-=size
                report['files']+=1
                report['bytes']+=size
            except OSError:
                report['errors']+=1
    return report


def prune_timed(mapping, now, *, ttl=None):
    """Remove expired disposable values; pending tasks and history stay untouched."""
    if not isinstance(mapping,dict):
        return 0
    removed=0
    for key,value in list(mapping.items()):
        stamp=value[0] if isinstance(value,(tuple,list)) and value else value
        if not isinstance(stamp,(float,int)) or isinstance(stamp,bool) or not math.isfinite(stamp):
            continue
        expired=now-stamp>ttl if ttl is not None else stamp<=now
        if expired:
            mapping.pop(key,None)
            removed+=1
    return removed


def release_expired(bot, now=None):
    now=time.monotonic() if now is None else now
    removed=0
    vision=getattr(bot,'vision_recognizer',None)
    if vision:
        removed+=prune_timed(vision.cache,now)
        removed+=prune_timed(vision.backoff,now)
    extensions=getattr(bot,'extensions',None)
    if extensions:
        for name,ttl in (('search_cache',600),('weather_cache',600),('source_records',1800)):
            removed+=prune_timed(getattr(extensions,name,None),now,ttl=ttl)
        aggregator=getattr(extensions,'weather_aggregator',None)
        if aggregator:
            removed+=prune_timed(aggregator.cache,now,ttl=600)
            removed+=prune_timed(aggregator.cooldowns,now)
    videos=getattr(bot,'videos',None)
    if videos:
        removed+=prune_timed(getattr(videos,'cache',None),now,ttl=600)
        removed+=prune_timed(getattr(videos,'messages',None),now,ttl=1800)
    return removed


async def maintenance_loop(bot, root, interval=1800):
    # Start after initialization, and never block the event loop with filesystem I/O.
    await asyncio.sleep(60)
    while True:
        try:
            removed=release_expired(bot)
            report=await asyncio.to_thread(cleanup_generated,root,dry_run=False)
            vision=getattr(bot,'vision_recognizer',None)
            cache=getattr(vision,'result_cache',None)
            rows=await asyncio.to_thread(cache.prune) if cache and hasattr(cache,'prune') else 0
            collected=await asyncio.to_thread(gc.collect)
            print('[Maintenance]: expired memory entries',removed,'vision rows',rows,
                  'generated files',report['files'],'disk MiB',round(report['bytes']/1024**2,2),
                  'GC objects',collected,'file errors',report['errors'])
            if vision:
                print('[AI cache counters]:',getattr(vision,'cache_stats',{}))
        except Exception as error:
            print('[Maintenance deferred]:',type(error).__name__)
        await asyncio.sleep(interval)
