#!/usr/bin/env python3
"""
Simple script to find your Telegram chat_id.

Steps:
  1) Create a new bot via @BotFather in Telegram and save the Token
  2) Send any message (e.g. "hi") to your bot from your account
  3) Run this script and paste in the Token when asked
  4) It will print your chat_id — put it in config.json
"""

import requests

def main():
    token = input("Enter your Bot Token: ").strip()
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    resp = requests.get(url, timeout=15)
    data = resp.json()

    if not data.get("ok"):
        print(f"❌ Error: {data}")
        return

    results = data.get("result", [])
    if not results:
        print("⚠️ No messages yet. Send a message to the bot first, then try again.")
        return

    seen = set()
    for update in results:
        message = update.get("message") or update.get("edited_message")
        if not message:
            continue
        chat = message["chat"]
        chat_id = chat["id"]
        if chat_id in seen:
            continue
        seen.add(chat_id)
        name = chat.get("first_name") or chat.get("title") or "?"
        print(f"✅ chat_id: {chat_id}  (from: {name})")


if __name__ == "__main__":
    main()
