"""همگام‌سازی پرامپت‌های default_prompts.py با دیتابیس.

seed_defaults فقط کلیدهای غایب را می‌سازد و نسخهٔ موجود را دست نمی‌زند. وقتی
متن پیش‌فرض را در کد عوض می‌کنیم، این اسکریپت نسخهٔ تازه می‌سازد و فعالش می‌کند.

    python -m scripts.sync_prompts            # فقط گزارش تفاوت
    python -m scripts.sync_prompts --apply    # اعمال
"""
import sys

from app.agent.default_prompts import DEFAULTS
from app.crud import active_prompt, add_prompt_version
from app.db import db_session


def main(apply: bool) -> None:
    changed = []
    with db_session() as session:
        for key, (_description, content) in DEFAULTS.items():
            current, version = active_prompt(session, key)
            if (current or "").strip() == content.strip():
                continue
            changed.append((key, version, len(current or ""), len(content)))
            if apply:
                add_prompt_version(session, key, content,
                                   notes="sync from default_prompts.py", activate=True)

    if not changed:
        print("همه‌چیز هم‌گام است.")
        return
    for key, version, old_len, new_len in changed:
        print(f"  {key}: v{version} ({old_len} chars) → {new_len} chars")
    print(("اعمال شد: " if apply else "برای اعمال --apply بزن. تعداد: ") + str(len(changed)))


if __name__ == "__main__":
    main("--apply" in sys.argv)
