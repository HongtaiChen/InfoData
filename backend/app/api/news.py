#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""资讯 API：财联社 + 东财双源新闻列表"""
from fastapi import APIRouter, Query

from ..db import query_all

router = APIRouter()


@router.get("")
def news_list(
    source: str = Query("", description="过滤来源：cls / em，缺省全部"),
    keyword: str = Query("", description="标题模糊搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """资讯列表（按发布时间倒序）"""
    where = []
    params: list = []
    if source:
        where.append("source = %s")
        params.append(source)
    if keyword:
        where.append("title LIKE %s")
        params.append(f"%{keyword}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    total = query_all(f"SELECT COUNT(*) AS n FROM news {where_sql}", params)[0]["n"]
    rows = query_all(
        f"""
        SELECT id, title, source, published_at, url
        FROM news
        {where_sql}
        ORDER BY published_at DESC
        LIMIT %s OFFSET %s
        """,
        params + [page_size, (page - 1) * page_size],
    )
    return {"total": total, "page": page, "page_size": page_size, "items": rows}


@router.get("/{news_id}")
def news_detail(news_id: int):
    """资讯详情"""
    row = query_all(
        "SELECT id, title, source, published_at, content, url FROM news WHERE id=%s",
        [news_id],
    )
    return {"item": row[0] if row else None}
