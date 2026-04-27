import time
import requests
import os
import argparse
import re
import json

def parse(content):
    data = {}
    blocks = re.findall(r'\[(\d+)\]\s*=\s*\{(.*?)\}', content, re.DOTALL)
    for ach_id, inner in blocks:
        details = {}
        fields = re.findall(r'\["(.*?)"\]\s*=\s*(.*?)[,\n]', inner)
        for key, val in fields:
            val = val.strip().strip('"')
            if val == "true": val = True
            elif val == "false": val = False
            elif val == "nil": val = None
            try:
                if isinstance(val, str) and val.isdigit(): val = int(val)
            except: pass
            details[key] = val
        data[ach_id] = details
    return data

def upload(path, url):
    last_mtime = 0
    while True:
        try:
            if os.path.exists(path):
                mtime = os.path.getmtime(path)
                if mtime > last_mtime:
                    with open(path, 'r', encoding='utf-8') as f:
                        raw = f.read()
                    payload = parse(raw)
                    if payload:
                        r = requests.post(url, json=payload)
                        status = "OK" if r.status_code == 200 else f"ERR {r.status_code}"
                        count = len(payload)
                        done = sum(1 for a in payload.values() if a.get('done'))
                        print(f"[{status}] Sent {count} achievements ({done} completed)")
                    last_mtime = mtime
        except Exception as e:
            print(f"[ERROR] {e}")
        time.sleep(30)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--path', required=True)
    parser.add_argument('--url', required=True)
    args = parser.parse_args()
    upload(args.path, args.url)

