from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
import json
import threading

from database.database import get_db, SessionLocal
from models.business_settings import BusinessSettings

router = APIRouter(prefix="/api/business-settings", tags=["业务设置"])


# ============ Pydantic Models ============
class DailySalesWeight(BaseModel):
    period: str
    label: str
    weight: float


class DailySalesConfig(BaseModel):
    type: str
    weights: List[DailySalesWeight]


class BusinessSettingResponse(BaseModel):
    id: int
    setting_type: str
    setting_name: str
    formula_config: DailySalesConfig
    is_active: int


# ============ 默认公式配置 ============
DEFAULT_DAILY_SALES_CONFIG = DailySalesConfig(
    type="weighted",
    weights=[
        DailySalesWeight(period="3d", label="3天日均", weight=0.0),
        DailySalesWeight(period="7d", label="7天日均", weight=0.2),
        DailySalesWeight(period="14d", label="14天日均", weight=0.2),
        DailySalesWeight(period="30d", label="30天日均", weight=0.2),
        DailySalesWeight(period="60d", label="60天日均", weight=0.2),
        DailySalesWeight(period="90d", label="90天日均", weight=0.2),
    ]
)


# ============ API Routes ============
@router.get("/test")
async def test_route():
    """测试端点"""
    return {"message": "business_settings router is working", "prefix": router.prefix}


@router.get("/{setting_type}", response_model=BusinessSettingResponse)
async def get_setting(setting_type: str, db: Session = Depends(get_db)):
    """获取业务设置"""
    setting = db.query(BusinessSettings).filter(
        BusinessSettings.setting_type == setting_type
    ).first()

    if not setting:
        # 返回默认配置
        return BusinessSettingResponse(
            id=0,
            setting_type=setting_type,
            setting_name=setting_type,
            formula_config=DEFAULT_DAILY_SALES_CONFIG,
            is_active=1
        )

    return BusinessSettingResponse(
        id=setting.id,
        setting_type=setting.setting_type,
        setting_name=setting.setting_name,
        formula_config=DailySalesConfig(**json.loads(setting.formula_config)),
        is_active=setting.is_active
    )


@router.get("/", response_model=List[BusinessSettingResponse])
async def list_settings(db: Session = Depends(get_db)):
    """获取所有业务设置"""
    settings = db.query(BusinessSettings).all()

    result = []
    for s in settings:
        result.append(BusinessSettingResponse(
            id=s.id,
            setting_type=s.setting_type,
            setting_name=s.setting_name,
            formula_config=DailySalesConfig(**json.loads(s.formula_config)),
            is_active=s.is_active
        ))

    # 如果没有日均销量设置，返回默认值
    if not any(s.setting_type == "daily_sales" for s in settings):
        result.append(BusinessSettingResponse(
            id=0,
            setting_type="daily_sales",
            setting_name="日均销量公式",
            formula_config=DEFAULT_DAILY_SALES_CONFIG,
            is_active=1
        ))

    return result


# 全局锁，防止并发重新计算
_recalculate_lock = threading.Lock()
_recalculate_status = {"running": False, "message": ""}


def _trigger_recalculation_background(setting_type: str):
    """后台线程触发重新计算"""
    global _recalculate_status

    if setting_type != "daily_sales":
        return

    # 尝试获取锁
    if not _recalculate_lock.acquire(blocking=False):
        print("[INFO] 重新计算正在进行中，跳过")
        return

    try:
        _recalculate_status["running"] = True
        _recalculate_status["message"] = "开始重新计算..."
        print("[INFO] 后台开始重新计算日均销量和补货决策...")

        # 创建新的数据库会话
        db = SessionLocal()
        try:
            from services.inventory_service import calculate_replenishment
            calc_result = calculate_replenishment(db)
            _recalculate_status["message"] = f"重新计算完成: {calc_result}"
            print(f"[INFO] 后台重新计算完成: {calc_result}")
        finally:
            db.close()
    except Exception as e:
        _recalculate_status["message"] = f"重新计算失败: {e}"
        print(f"[ERROR] 后台重新计算失败: {e}")
    finally:
        _recalculate_status["running"] = False
        _recalculate_lock.release()


@router.get("/recalculate/status")
async def get_recalculate_status():
    """获取重新计算状态"""
    return _recalculate_status


@router.put("/{setting_type}", response_model=BusinessSettingResponse)
async def update_setting(
    setting_type: str,
    request: dict,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """更新业务设置"""
    setting = db.query(BusinessSettings).filter(
        BusinessSettings.setting_type == setting_type
    ).first()

    formula_config = request.get("formula_config")
    is_active = request.get("is_active", 1)

    if setting:
        if formula_config:
            setting.formula_config = json.dumps(formula_config, ensure_ascii=False)
        setting.is_active = is_active
    else:
        setting = BusinessSettings(
            tenant_id=1,
            setting_type=setting_type,
            setting_name="日均销量公式" if setting_type == "daily_sales" else setting_type,
            formula_config=json.dumps(formula_config, ensure_ascii=False) if formula_config else json.dumps(DEFAULT_DAILY_SALES_CONFIG.model_dump(), ensure_ascii=False),
            is_active=is_active
        )
        db.add(setting)

    db.commit()
    db.refresh(setting)

    # 在后台触发重新计算
    recalculate_triggered = False
    if setting_type == "daily_sales":
        background_tasks.add_task(_trigger_recalculation_background, setting_type)
        recalculate_triggered = True

    result = BusinessSettingResponse(
        id=setting.id,
        setting_type=setting.setting_type,
        setting_name=setting.setting_name,
        formula_config=DailySalesConfig(**json.loads(setting.formula_config)),
        is_active=setting.is_active
    )

    result_dict = result.model_dump()
    result_dict["recalculate_triggered"] = recalculate_triggered
    return result_dict


@router.post("/reset/{setting_type}", response_model=BusinessSettingResponse)
async def reset_setting(
    setting_type: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """重置为默认配置"""
    setting = db.query(BusinessSettings).filter(
        BusinessSettings.setting_type == setting_type
    ).first()

    default_json = DEFAULT_DAILY_SALES_CONFIG.model_dump_json(ensure_ascii=False)

    if setting:
        setting.formula_config = default_json
        db.commit()
        db.refresh(setting)
    else:
        setting = BusinessSettings(
            tenant_id=1,
            setting_type=setting_type,
            setting_name="日均销量公式" if setting_type == "daily_sales" else setting_type,
            formula_config=default_json,
            is_active=1
        )
        db.add(setting)
        db.commit()
        db.refresh(setting)

    # 在后台触发重新计算
    recalculate_triggered = False
    if setting_type == "daily_sales":
        background_tasks.add_task(_trigger_recalculation_background, setting_type)
        recalculate_triggered = True

    result = BusinessSettingResponse(
        id=setting.id,
        setting_type=setting.setting_type,
        setting_name=setting.setting_name,
        formula_config=DEFAULT_DAILY_SALES_CONFIG,
        is_active=setting.is_active
    )

    result_dict = result.model_dump()
    result_dict["recalculate_triggered"] = recalculate_triggered
    return result_dict
