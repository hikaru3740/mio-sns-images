#!/usr/bin/env python3
"""MIO Threads 自動投稿（GitHub Actions 用）— 2026-10-10 Claude Code
threads/queue.json の pending のうち、時刻が来たものを1本だけ公式 Threads API で公開する。
- 時刻を過ぎた pending が複数あるときは、いちばん新しいものだけ公開し、古いものは missed（連投しない）
- 予定から3時間以上過ぎたものは missed
- 前の公開から40分以内なら何もしない
- 結果が分からないときは unknown にして再送しない
トークンは環境変数 THREADS_TOKEN（リポジトリの Secret）。ログにトークンを出さない。
"""
import datetime as dt, json, os, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

Q = Path(__file__).with_name("queue.json")
API = "https://graph.threads.net/v1.0"
JST = dt.timezone(dt.timedelta(hours=9))
TOKEN = os.environ.get("THREADS_TOKEN", "")
UID = os.environ.get("THREADS_USER_ID", "")


def req(method, url, params):
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        url, data = url + "?" + data.decode(), None
    r = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as res:
            return json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from None


def main():
    if not TOKEN or not UID:
        raise SystemExit("THREADS_TOKEN / THREADS_USER_ID が未設定です")
    q = json.loads(Q.read_text())
    now = dt.datetime.now(JST)
    items = q["items"]
    last = max((dt.datetime.fromisoformat(i["posted_at"]) for i in items if i.get("posted_at")), default=None)
    due = [i for i in items if i["status"] == "pending" and dt.datetime.fromisoformat(i["scheduled_at"]) <= now]
    for i in due:
        if now - dt.datetime.fromisoformat(i["scheduled_at"]) > dt.timedelta(hours=3):
            i["status"] = "missed"
    due = [i for i in due if i["status"] == "pending"]
    if len(due) > 1:
        for i in due[:-1]:
            i["status"] = "missed"
        due = due[-1:]
    if due and (last is None or now - last >= dt.timedelta(minutes=40)):
        it = due[0]
        p = {"text": it["text"], "access_token": TOKEN}
        if it.get("image_url"):
            p.update(media_type="IMAGE", image_url=it["image_url"])
        else:
            p["media_type"] = "TEXT"
        if it.get("topic_tag"):
            p["topic_tag"] = it["topic_tag"]
        try:
            try:
                c = req("POST", f"{API}/{UID}/threads", p)
            except RuntimeError as e:
                if "topic_tag" in p and "topic_tag" in str(e):
                    p.pop("topic_tag"); c = req("POST", f"{API}/{UID}/threads", p)
                else:
                    raise
            for _ in range(12):
                st = req("GET", f"{API}/{c['id']}", {"fields": "status", "access_token": TOKEN})
                if st.get("status") == "FINISHED":
                    break
                if st.get("status") in ("ERROR", "EXPIRED"):
                    raise RuntimeError(f"container {st}")
                time.sleep(5)
            r = req("POST", f"{API}/{UID}/threads_publish", {"creation_id": c["id"], "access_token": TOKEN})
            try:
                link = req("GET", f"{API}/{r['id']}", {"fields": "permalink", "access_token": TOKEN}).get("permalink")
            except Exception:
                link = None
            it.update(status="posted", media_id=r["id"], permalink=link, posted_at=dt.datetime.now(JST).isoformat())
            print("posted", it["id"], link)
        except RuntimeError as e:
            it.update(status="failed", error=str(e)[:300], failed_at=now.isoformat()); print("failed", it["id"], str(e)[:200])
        except Exception as e:
            it.update(status="unknown", error=repr(e)[:300], failed_at=now.isoformat()); print("unknown", it["id"], repr(e)[:200])
    else:
        print("nothing to post now", now.isoformat(), "due:", len(due))
    Q.write_text(json.dumps(q, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
