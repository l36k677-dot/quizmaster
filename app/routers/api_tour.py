"""新手指引状态接口。

只做一件事：把「这位用户已经看过指引」记到账号上。

为什么落在服务端而不是只写 localStorage：
  1. 换浏览器、换设备、清缓存之后不该再被同一份指引拦一次；
  2. 与项目其它状态一样，「谁看过了」是可被管理端审计的事实，
     而不是某个浏览器的本地偏好；
  3. 前端依然读一次 localStorage（避免首屏闪一下），但**权威在服务端** ——
     两边不一致时以服务端为准，前端会把本地标记补上。

接口不接受任何客户端传入的步骤内容或进度，只有「已看过」这一个布尔事实。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.response import ok
from app.models import User
from app.routers.deps import current_user
from app.services import tour as tour_service

router = APIRouter(tags=["tour"])


@router.get("/api/tour/state")
def state(user: User = Depends(current_user)):
    """当前账号是否看过指引（页面渲染不走这里，但便于外部核对与演示）。"""
    return ok({
        "seen": bool(user.tour_seen),
        "version": tour_service.TOUR_VERSION,
        "stepCount": len(tour_service.TOUR_STEPS),
    })


@router.post("/api/tour/seen")
def mark_seen(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """记下「已看过」（幂等）。

    前端在**指引开场那一刻**就调它（不等走完，也不等点跳过）——
    记的是「展示过」而不是「学完了」：用户中途关掉页面，下次进首页
    不该被同一份指引再拦一次；想重看时账户菜单里有入口。
    """
    if not user.tour_seen:
        user.tour_seen = True
        db.add(user)
        db.commit()
    return ok({
        "seen": True,
        "version": tour_service.TOUR_VERSION,
        "stepCount": len(tour_service.TOUR_STEPS),
    })
