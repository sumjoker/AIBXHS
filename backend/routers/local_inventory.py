"""
本地仓库存API路由
"""
import logging
from datetime import date
from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File
from typing import Optional
from sqlalchemy.orm import Session
from database.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/local-inventory", tags=["local-inventory"])


@router.post("/import")
async def import_local_inventory(
    file: UploadFile = File(..., description="本地仓库存Excel文件"),
    db: Session = Depends(get_db)
):
    """
    导入本地仓库存Excel文件
    Excel格式要求：至少包含 ASIN（或SKU）和 库存数量 列
    支持的列名：ASIN, SKU, 品名, 店铺, 国家, 本地库存/本地仓库存/库存数量/数量
    """
    try:
        from services.local_inventory_service import import_local_inventory

        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="上传的文件为空")

        result = import_local_inventory(db, file_content=content, filename=file.filename)
        return {"success": True, "data": result}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"导入本地仓库存失败: {e}")
        raise HTTPException(status_code=500, detail=f"导入本地仓库存失败: {str(e)}")


@router.get("/summary")
async def get_local_inventory_summary(db: Session = Depends(get_db)):
    """获取本地仓库存汇总统计"""
    try:
        from services.local_inventory_service import get_local_inventory_summary
        result = get_local_inventory_summary(db)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"获取本地仓库存汇总失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取本地仓库存汇总失败: {str(e)}")


@router.get("/list")
async def get_local_inventory_list(
    keyword: Optional[str] = Query(None, description="搜索关键词（ASIN/SKU/品名/店铺）"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    db: Session = Depends(get_db)
):
    """查询本地仓库存列表"""
    try:
        from services.local_inventory_service import get_local_inventory_list
        result = get_local_inventory_list(db, keyword=keyword, page=page, page_size=page_size)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"查询本地仓库存列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"查询本地仓库存列表失败: {str(e)}")


@router.delete("/clear")
async def clear_local_inventory(db: Session = Depends(get_db)):
    """清空本地仓库存数据"""
    try:
        from services.local_inventory_service import clear_local_inventory
        result = clear_local_inventory(db)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"清空本地仓库存失败: {e}")
        raise HTTPException(status_code=500, detail=f"清空本地仓库存失败: {str(e)}")


@router.post("/import-reduction")
async def import_reduction_table(
    country: str = Query(..., description="国家（必填）"),
    file: UploadFile = File(..., description="减表Excel文件"),
    db: Session = Depends(get_db)
):
    """
    导入减表Excel文件到本地仓库存
    Excel需包含 SKU（FNSKU）和 已采购 列
    以 (country, sku) 为唯一键 UPSERT
    """
    try:
        from services.local_inventory_service import import_reduction_table

        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="上传的文件为空")

        result = import_reduction_table(db, country=country, file_content=content)
        return {"success": True, "data": result}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"导入减表失败: {e}")
        raise HTTPException(status_code=500, detail=f"导入减表失败: {str(e)}")


@router.get("/import-reduction/result/{file_id}")
async def download_reduction_result(file_id: str):
    """
    下载减表导入结果反馈Excel文件
    """
    import os
    import tempfile
    from fastapi.responses import FileResponse

    try:
        file_path = os.path.join(tempfile.gettempdir(), file_id)
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="结果文件不存在或已过期")

        today = date.today().isoformat()
        return FileResponse(
            path=file_path,
            filename=f"减表导入结果_{today}.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"下载结果文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"下载结果文件失败: {str(e)}")
