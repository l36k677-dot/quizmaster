"""初始化数据库：建表 + 导入分类 / 题库 / 成就定义 / 判分口径 / 讲义语料。

用法：
    uv run python scripts/init_db.py            # 幂等初始化（已存在则跳过）
    uv run python scripts/init_db.py --reset    # 删除数据库后重建

这里只播种**字典数据**（分类、题库、成就定义、判分口径、讲义语料）与一个管理账号。
刻意不预置学生账号，也不预置任何历史成绩 —— 排行榜、首页统计与成就墙上出现的，
都应当是使用者自己跑出来的真实记录；混入「演示用户」会让人分不清哪些是自己的成绩。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.clock import utcnow                       # noqa: E402
from app.core.config import BACKUP_DIR, DB_PATH, SEED_DIR, settings  # noqa: E402
from app.core.db import SessionLocal, engine, init_db   # noqa: E402
from app.core.security import hash_password, new_id     # noqa: E402
from app.models import (                                # noqa: E402
    JUDGE_OPTIONS,
    Achievement,
    Category,
    Question,
    ScoreRule,
    User,
    dump_keywords,
)
from app.services import rewards as reward_service       # noqa: E402
from app.rag import service as rag_service               # noqa: E402

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "QuizAdmin@2026"
ADMIN_DISPLAY_NAME = "系统管理员"

CATEGORIES = [
    ("cat_create", "创校与发展", "南开创办、抗战内迁、复校与校区变迁", 10),
    ("cat_people", "人物与校友", "创办人、杰出校友与学术名家", 20),
    ("cat_campus", "校园与文化", "校训、校歌、校名、校徽与校园建筑", 30),
    ("cat_spirit", "精神与学科", "精神传统与学科建设", 40),
]


def reset_database() -> None:
    """删除数据库文件并备份旧库。"""
    if DB_PATH.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = utcnow().strftime("%Y%m%d-%H%M%S")
        target = BACKUP_DIR / f"quizmaster-{stamp}.db"
        DB_PATH.replace(target)
        print(f"[reset] 旧数据库已备份到 {target}")
    engine.dispose()


def seed_categories(db) -> dict[str, Category]:
    mapping: dict[str, Category] = {}
    for cat_id, name, description, order in CATEGORIES:
        row = db.get(Category, cat_id)
        if row is None:
            row = Category(
                id=cat_id, name=name, description=description, sort_order=order, is_active=True
            )
            db.add(row)
            print(f"[seed] 分类 + {name}")
        mapping[name] = row
    db.commit()
    return mapping


def _question_options(item: dict) -> dict[str, str]:
    """补齐 A–D 四个键。

    单选必须自带 A–D；判断题用固定的「正确 / 错误」（复用 A/B 键位，C/D 留空）；
    填空题没有选项，四项都留空 —— 模型侧按「非空才渲染」处理，
    所以留空的选项不会在答题页上冒出来。
    """
    qtype = (item.get("type") or "SINGLE").upper()
    blank_options = {"A": "", "B": "", "C": "", "D": ""}
    if qtype == "JUDGE":
        merged = dict(blank_options)
        merged.update(JUDGE_OPTIONS)
        return merged
    if qtype == "BLANK":
        return blank_options
    options = item["options"]
    return {key: str(options.get(key, "") or "") for key in ("A", "B", "C", "D")}


def seed_questions(db, categories: dict[str, Category], admin: User) -> int:
    path = SEED_DIR / "questions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    created = 0
    skipped = 0
    for item in data["questions"]:
        if db.get(Question, item["id"]) is not None:
            # 只补新增题；已存在的题不覆盖 —— 管理员可能已经在页面上改过内容
            skipped += 1
            continue
        category = categories[item["category"]]
        options = _question_options(item)
        qtype = (item.get("type") or "SINGLE").upper()
        db.add(
            Question(
                id=item["id"],
                content=item["content"],
                qtype=qtype,
                option_a=options["A"],
                option_b=options["B"],
                option_c=options["C"],
                option_d=options["D"],
                correct_answer=str(item["correctAnswer"]),
                keywords_json=dump_keywords(item.get("keywords") or []),
                explanation=item["explanation"],
                difficulty=item["difficulty"],
                category_id=category.id,
                source_title=item.get("sourceTitle"),
                source_section=item.get("sourceSection"),
                is_active=True,
                created_by=admin.id,
            )
        )
        created += 1
    db.commit()
    print(
        f"[seed] 题目 + {created} 道（新增）/ 跳过 {skipped} 道（已存在）"
        f"，种子共 {len(data['questions'])} 道"
    )
    return created


def seed_achievements(db) -> tuple[int, int]:
    """播种成就定义（新增 + 同步修订）。

    成就定义属于**字典数据**，不是用户数据。旧版只做 insert-if-absent，
    于是修订一处名称（例如把「十局学者」改成「渐入佳境」）时，
    已经存在的库会一直显示旧名，除非手动删库重建。
    现在改成：已存在则同步 name / description / rule_type / threshold / sort_order。

    两个字段刻意不动：
      * ``code``     —— user_achievements 靠它关联，改了会断开关联；
      * ``is_active`` —— 那是管理员在页面上的启停决定，不该被每次播种重置。
    """
    created = 0
    updated = 0
    for item in reward_service.ACHIEVEMENT_SEED:
        row = db.get(Achievement, item["code"])
        if row is None:
            db.add(Achievement(**item, is_active=True))
            created += 1
            continue
        changed = False
        for field in ("name", "description", "rule_type", "threshold", "sort_order"):
            value = item[field]
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed = True
        if changed:
            updated += 1
    db.commit()
    print(
        f"[seed] 成就 + {created} 条 / 同步 {updated} 条"
        f"（共 {len(reward_service.ACHIEVEMENT_SEED)} 条）"
    )
    return created, updated


def seed_score_rules(db) -> None:
    if db.get(ScoreRule, "A-1") is None:
        db.add(
            ScoreRule(
                version="A-1",
                scoring_mode="ACCURACY",
                payload_json=json.dumps({"formula": "round(correct*100/total)"}),
                description="正确率口径：score = round(correct_count × 100 / question_count)",
                is_active=True,
            )
        )
        print("[seed] 判分口径 + A-1（正确率口径，默认启用）")
    if db.get(ScoreRule, "B-1") is None:
        db.add(
            ScoreRule(
                version="B-1",
                scoring_mode="WEIGHTED",
                payload_json=json.dumps({"weights": {"EASY": 1, "MEDIUM": 1.5, "HARD": 2}}),
                description="难度加权口径（默认关闭，启用需新开会话指向该版本）",
                is_active=False,
            )
        )
        print("[seed] 判分口径 + B-1（难度加权，默认关闭）")
    db.commit()


def seed_admin(db) -> User:
    """播种唯一的管理账号。

    学生账号一律由使用者自行注册：预置的「演示用户」会挤进排行榜、
    让首页统计看起来像是别人的数据，也让首次进入的人分不清哪些是真实成绩。
    """
    row = db.query(User).filter(User.username == ADMIN_USERNAME).one_or_none()
    if row is None:
        row = User(
            id=new_id("u_"),
            username=ADMIN_USERNAME,
            password_hash=hash_password(ADMIN_PASSWORD),
            role="ADMIN",
            display_name=ADMIN_DISPLAY_NAME,
            is_demo=False,
            study_points=0,
        )
        db.add(row)
        db.commit()
        print(f"[seed] 管理账号 + {ADMIN_USERNAME}")
    return row


def seed_corpus(db, admin: User) -> int:
    documents = rag_service.ingest_corpus_dir(db, created_by=admin.id)
    chunks = sum(doc.chunk_count for doc in documents)
    print(f"[seed] 语料 + {len(documents)} 份 / {chunks} 个切片")
    return len(documents)


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 QuizMaster 数据库")
    parser.add_argument("--reset", action="store_true", help="删除数据库后重建")
    args = parser.parse_args()

    if args.reset:
        reset_database()

    init_db()
    print(f"[init] 数据库：{settings.sqlalchemy_url}")

    with SessionLocal() as db:
        admin = seed_admin(db)
        categories = seed_categories(db)
        seed_questions(db, categories, admin)
        seed_achievements(db)
        seed_score_rules(db)
        seed_corpus(db, admin)

        total_questions = db.query(Question).count()
        active_questions = db.query(Question).filter(Question.is_active.is_(True)).count()

    print("\n=== 初始化完成 ===")
    print(f"题库：{active_questions} / {total_questions} 道启用")
    print(f"管理账号：{ADMIN_USERNAME} / {ADMIN_PASSWORD}（题库与资料库管理）")
    print("竞答账号：请在登录页点击「注册」自行创建，成绩只属于该账号")
    print("启动：uv run python run.py    （没有 uv 时用 .venv 里的 python run.py）")
    print("      或直接双击 start.bat / bash start.sh，两条路径都会自动选对")


if __name__ == "__main__":
    main()
