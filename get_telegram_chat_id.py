#!/usr/bin/env python3
"""
سكريبت بسيط عشان تعرف الـ chat_id بتاعك في تليجرام.

الخطوات:
  1) اعمل بوت جديد عن طريق @BotFather في تليجرام واحفظ الـ Token
  2) ابعت أي رسالة (زي "hi") للبوت بتاعك من حسابك
  3) شغل السكريبت ده وحط الـ Token لما يطلبه منك
  4) هيطبعلك الـ chat_id، حطه في config.json
"""

import requests

def main():
    token = input("حط Bot Token بتاعك: ").strip()
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    resp = requests.get(url, timeout=15)
    data = resp.json()

    if not data.get("ok"):
        print(f"❌ خطأ: {data}")
        return

    results = data.get("result", [])
    if not results:
        print("⚠️ مفيش رسايل لسه. ابعت رسالة للبوت الأول من تليجرام وجرب تاني.")
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
        print(f"✅ chat_id: {chat_id}  (من: {name})")


if __name__ == "__main__":
    main()
