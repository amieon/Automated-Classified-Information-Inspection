import os
import re
import sys
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from pathlib import Path
from .base_checker import BaseChecker
from utils.leak_detector import LeakDetector


# ==================== 数据库连接器 ====================

class DBConnector:
    """数据库连接器，支持 MySQL（通过 pymysql）"""

    def __init__(self, db_type: str = "mysql", host: str = "localhost", port: int = 3306,
                 user: str = "", password: str = "", database: str = "", dbname: str = ""):
        self.db_type = db_type.lower()
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database if database else dbname
        self.conn = None
        self.cursor = None

    def connect(self) -> bool:
        """建立数据库连接"""
        try:
            if self.db_type == "mysql":
                import pymysql
                self.conn = pymysql.connect(
                    host=self.host,
                    port=self.port,
                    user=self.user,
                    password=self.password,
                    database=self.database,
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor  # 返回字典格式结果
                )
                self.cursor = self.conn.cursor()
            elif self.db_type == "sqlite":
                import sqlite3
                db_path = self.database if self.database else ":memory:"
                self.conn = sqlite3.connect(db_path)
                self.conn.row_factory = sqlite3.Row
                self.cursor = self.conn.cursor()
            else:
                return False
            return True
        except ImportError as e:
            print(f"⚠️ 数据库驱动未安装: {e}")
            return False
        except Exception as e:
            print(f"⚠️ 数据库连接失败: {e}")
            return False

    def disconnect(self):
        """关闭连接"""
        if self.cursor:
            try:
                self.cursor.close()
            except:
                pass
        if self.conn:
            try:
                self.conn.close()
            except:
                pass

    def get_tables(self) -> List[str]:
        tables = []
        try:
            if self.db_type == "mysql":
                # print(f"🔍 db_type = {self.db_type!r}, database = {self.database!r}")
                # print(f"🔍 cursor = {self.cursor}, cursor.__class__ = {self.cursor.__class__}")
                # 使用 INFORMATION_SCHEMA 代替 SHOW TABLES，更稳定
                sql = "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME"
                self.cursor.execute(sql, (self.database,))
                for row in self.cursor.fetchall():
                    # 兼容 DictCursor 和普通游标
                    if isinstance(row, dict):
                        tables.append(row['TABLE_NAME'])
                    else:
                        tables.append(row[0])
            elif self.db_type == "sqlite":
                self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                tables = [row[0] for row in self.cursor.fetchall()]
        except Exception as e:
            import traceback
            print(f"⚠️ 获取表名失败: {e}")
            traceback.print_exc()  # 打印完整错误栈，方便定位
        return tables

    def get_columns(self, table_name: str) -> List[Dict]:
        """获取指定表的所有列信息"""
        columns = []
        try:
            if self.db_type == "mysql":
                self.cursor.execute(f"SHOW FULL COLUMNS FROM `{table_name}`")
                columns = self.cursor.fetchall()
            elif self.db_type == "sqlite":
                self.cursor.execute(f"PRAGMA table_info(`{table_name}`)")
                rows = self.cursor.fetchall()
                for row in rows:
                    columns.append({
                        "Field": row["name"],
                        "Type": row["type"],
                        "Key": "PRI" if row["pk"] else ""
                    })
        except Exception as e:
            print(f"⚠️ 获取 {table_name} 列信息失败: {e}")
        return columns

    def is_text_column(self, col_type: str) -> bool:
        """判断列类型是否为文本类型（需要搜索的）"""
        if not col_type:
            return False
        col_type = col_type.lower()
        text_types = ['char', 'varchar', 'text', 'tinytext', 'mediumtext', 'longtext',
                      'blob', 'mediumblob', 'longblob', 'json', 'enum', 'set']
        for t in text_types:
            if col_type.startswith(t):
                return True
        return False

    def get_primary_key(self, table_name: str) -> Optional[str]:
        """获取表的主键字段名"""
        try:
            columns = self.get_columns(table_name)
            for col in columns:
                key = col.get("Key", "")
                if key == "PRI":
                    return col.get("Field", "")
        except:
            pass
        return None

    def search_in_table(self, table_name: str, columns: List[Dict],
                        keywords: List[str], detector: LeakDetector) -> List[Dict]:
        """
        在指定表的文本列中搜索关键词
        返回: [{"table":表名, "row_pk":主键值, "column":列名, "content":内容, "keyword":匹配到的关键词}, ...]
        """
        results = []

        # 筛选出文本类型的列
        text_columns = []
        for col in columns:
            field_name = col.get("Field", "")
            col_type = col.get("Type", "")
            if self.is_text_column(col_type):
                text_columns.append(field_name)

        if not text_columns:
            return results

        # 获取主键
        pk = self.get_primary_key(table_name)

        try:
            # 构建查询：查询所有文本列
            col_str = ", ".join([f"`{c}`" for c in text_columns])
            if pk:
                select_str = f"`{pk}`, {col_str}"
            else:
                select_str = col_str

            # 分批查询以避免内存溢出
            batch_size = 500
            offset = 0

            while True:
                if self.db_type == "mysql":
                    self.cursor.execute(f"SELECT {select_str} FROM `{table_name}` LIMIT {batch_size} OFFSET {offset}")
                else:
                    self.cursor.execute(f"SELECT {select_str} FROM `{table_name}` LIMIT {batch_size} OFFSET {offset}")

                rows = self.cursor.fetchall()
                if not rows:
                    break

                for row in rows:
                    # 对每个文本列进行关键词检测
                    for col_name in text_columns:
                        value = row.get(col_name)
                        if value is None or value == "":
                            continue

                        content = str(value)
                        # 使用 leak_detector 检查该内容
                        leak_lines = detector.check_text(content)
                        if leak_lines:
                            for line_no, keyword, matched_content in leak_lines:
                                result = {
                                    "table": table_name,
                                    "row_pk": str(row.get(pk, "N/A")) if pk else f"offset_{offset}",
                                    "column": col_name,
                                    "keyword": keyword,
                                    "content": matched_content[:200]  # 截取前200字符
                                }
                                results.append(result)

                offset += batch_size

                # 如果返回行数小于 batch_size，说明已经查完
                if len(rows) < batch_size:
                    break

        except Exception as e:
            print(f"⚠️ 搜索 {table_name} 失败: {e}")

        return results


# ==================== 数据库检查器模块 ====================

class DBCheckerModule(BaseChecker):
    # 数据库配置模板（用于前端提示）
    DB_CONFIG_TEMPLATE = {
        "mysql": {
            "host": "localhost",
            "port": 3306,
            "user": "root",
            "password": "",
            "database": "your_database"
        },
        "sqlite": {
            "path": "/path/to/database.db"
        }
    }

    def register_routes(self, app: FastAPI):

        # ------ 检查数据库（通过连接参数）------
        @app.post("/check/db/connect", response_class=HTMLResponse)
        async def check_db_connect(
                db_type: str = Form("mysql"),
                host: str = Form("localhost"),
                port: int = Form(3306),
                user: str = Form(""),
                password: str = Form(""),
                database: str = Form(""),
                dbname: str = Form(""),
        ):
            detector = LeakDetector()

            # 连接数据库
            connector = DBConnector(
                db_type=db_type,
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                dbname=dbname
            )

            if not connector.connect():
                return HTMLResponse(content=f"""
                <div class='alert alert-danger'>
                    ❌ 数据库连接失败！请检查连接参数。
                </div>
                """)

            try:
                # 获取所有表
                tables = connector.get_tables()
                if not tables:
                    return HTMLResponse(content=f"""
                    <div class='alert alert-warning'>
                        数据库连接成功，但未找到任何表。
                    </div>
                    """)

                # 遍历所有表进行搜索
                all_results = []
                total_tables = len(tables)
                searched_tables = 0

                for table_name in tables:
                    columns = connector.get_columns(table_name)
                    if not columns:
                        continue

                    table_results = connector.search_in_table(
                        table_name, columns, [], detector
                    )
                    if table_results:
                        all_results.extend(table_results)
                    searched_tables += 1

                # 构建结果HTML
                html = self._build_html_result(all_results, tables, searched_tables)
                # ========== ★ 新增：生成纯文本报告并写入全局变量 ==========
                text_report = self._generate_text_report(
                    results=all_results,
                    all_tables=tables,
                    searched_tables=searched_tables,
                    db_info=f"MySQL {host}:{port}/{database or dbname}"
                )
                # 写入主模块的 LATEST_REPORT 变量
                main_module = sys.modules.get('__main__')
                if main_module:
                    main_module.LATEST_REPORT = text_report
                # ================================================
                return HTMLResponse(content=html)

            finally:
                connector.disconnect()

        # ------ 检查数据库（通过SQLite文件路径）------
        @app.post("/check/db/path", response_class=HTMLResponse)
        async def check_db_path(path: str = Form(...)):
            detector = LeakDetector()

            p = Path(path)
            if not p.exists():
                return HTMLResponse(content=f"""
                <div class='alert alert-danger'>路径不存在: {path}</div>
                """)

            if not p.suffix.lower() in ['.db', '.sqlite', '.sqlite3', '.db3']:
                return HTMLResponse(content=f"""
                <div class='alert alert-warning'>文件 {p.name} 不是SQLite数据库文件</div>
                """)

            connector = DBConnector(
                db_type="sqlite",
                database=str(p)
            )

            if not connector.connect():
                return HTMLResponse(content=f"""
                <div class='alert alert-danger'>
                    ❌ SQLite数据库打开失败！
                </div>
                """)

            try:
                tables = connector.get_tables()
                if not tables:
                    return HTMLResponse(content=f"""
                    <div class='alert alert-warning'>
                        数据库打开成功，但未找到任何表。
                    </div>
                    """)

                all_results = []
                total_tables = len(tables)
                searched_tables = 0

                for table_name in tables:
                    columns = connector.get_columns(table_name)
                    if not columns:
                        continue

                    table_results = connector.search_in_table(
                        table_name, columns, [], detector
                    )
                    if table_results:
                        all_results.extend(table_results)
                    searched_tables += 1

                html = self._build_html_result(all_results, tables, searched_tables, db_path=str(p))
                # ========== ★ 新增：生成纯文本报告并写入全局变量 ==========
                text_report = self._generate_text_report(
                    results=all_results,
                    all_tables=tables,
                    searched_tables=searched_tables,
                    db_info=f"SQLite 文件: {p}"
                )
                main_module = sys.modules.get('__main__')
                if main_module:
                    main_module.LATEST_REPORT = text_report
                # ================================================
                return HTMLResponse(content=html)

            finally:
                connector.disconnect()

    # ==================== 内部方法 ====================
    def _generate_text_report(self, results: list, all_tables: list,
                              searched_tables: int, db_info: str) -> str:
        """生成可供下载的纯文本检查报告"""
        lines = []
        lines.append("=" * 60)
        lines.append("          数据库涉密数据检查报告")
        lines.append("=" * 60)
        lines.append(f"数据库信息: {db_info}")
        lines.append(f"检查时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"扫描表数: {searched_tables}")
        lines.append(f"涉密数据条数: {len(results)}")
        affected_tables = len(set(r['table'] for r in results))
        lines.append(f"涉及表数: {affected_tables}")
        lines.append("-" * 60)
        if not results:
            lines.append("未发现涉密数据。")
        else:
            # 按表分组
            table_groups = {}
            for r in results:
                tbl = r['table']
                table_groups.setdefault(tbl, []).append(r)
            lines.append("详细结果：")
            for table_name, items in table_groups.items():
                lines.append(f"\n【表名】{table_name} (共 {len(items)} 条)")
                for item in items:
                    row_pk = item.get('row_pk', 'N/A')
                    column = item.get('column', '')
                    keyword = item.get('keyword', '')
                    content = item.get('content', '')
                    lines.append(f"  行 {row_pk} | 字段 {column} | 关键词 [{keyword}] → {content}")
        lines.append("=" * 60)
        lines.append("报告结束")
        return "\n".join(lines)

    def _build_html_result(self, results: list, all_tables: list,
                           searched_tables: int, db_path: str = "") -> str:
        """生成结果HTML表格"""
        import html as html_mod

        # 统计
        total_tables = len(all_tables)
        leak_count = len(results)
        affected_tables = len(set(r['table'] for r in results))

        # 按表分组
        table_groups = {}
        for r in results:
            tbl = r['table']
            if tbl not in table_groups:
                table_groups[tbl] = []
            table_groups[tbl].append(r)

        db_info = f"数据库路径: {html_mod.escape(db_path)}" if db_path else "通过连接参数连接"

        html = f"""
        <h3>🗄️ 数据库检查结果</h3>
        <div class="alert alert-info">
            <strong>{db_info}</strong><br>
            共扫描 <strong>{searched_tables}</strong> 个表，发现 <strong>{leak_count}</strong> 处涉密数据，
            涉及 <strong>{affected_tables}</strong> 个表
        </div>
        """

        if not results:
            html += """
            <div class="alert alert-success">
                ✅ 未发现涉密数据！
            </div>
            """
            return html

        # 按表展示
        for table_name, table_results in table_groups.items():
            html += f"""
            <div class="card mb-4">
                <div class="card-header" style="background: linear-gradient(135deg, #667eea, #764ba2); color: white;">
                    <strong>📋 表：{html_mod.escape(table_name)}</strong>
                    <span class="badge bg-light text-dark ms-2">发现 {len(table_results)} 处</span>
                </div>
                <div class="card-body p-0">
                    <table class="table table-bordered mb-0 result-table">
                        <thead>
                            <tr>
                                <th style="width:100px">行ID</th>
                                <th style="width:150px">字段名</th>
                                <th style="width:120px">匹配关键词</th>
                                <th>内容（前200字符）</th>
                            </tr>
                        </thead>
                        <tbody>
            """

            for r in table_results:
                row_pk = html_mod.escape(r.get('row_pk', 'N/A'))
                column = html_mod.escape(r.get('column', ''))
                keyword = html_mod.escape(r.get('keyword', ''))
                content = html_mod.escape(r.get('content', ''))

                html += f"""
                <tr>
                    <td><code>{row_pk}</code></td>
                    <td><code>{column}</code></td>
                    <td><span class="highlight-badge">{keyword}</span></td>
                    <td><pre style="white-space:pre-wrap; word-wrap:break-word; max-height:100px; overflow-y:auto; margin:0; font-size:0.85rem;">{content}</pre></td>
                </tr>
                """

            html += """
                        </tbody>
                    </table>
                </div>
            </div>
            """

        # 添加归纳统计
        html += f"""
        <div class="card mt-3">
            <div class="card-header">
                <strong>📊 统计总览</strong>
            </div>
            <div class="card-body">
                <table class="table table-sm table-bordered">
                    <tr>
                        <th>数据库表总数</th>
                        <td>{total_tables}</td>
                    </tr>
                    <tr>
                        <th>已扫描表数</th>
                        <td>{searched_tables}</td>
                    </tr>
                    <tr>
                        <th>涉密数据条数</th>
                        <td><span class="highlight-badge">{leak_count}</span></td>
                    </tr>
                    <tr>
                        <th>涉及表数</th>
                        <td>{affected_tables}</td>
                    </tr>
                </table>
            </div>
        </div>
        """

        # 注入CSS样式（与主页面保持一致）
        html += """
        <style>
            .result-table th {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                font-weight: 600;
            }
            .highlight-badge {
                background: #ff6b6b;
                color: white;
                padding: 4px 12px;
                border-radius: 20px;
                font-size: 0.9rem;
                display: inline-block;
            }
            .card {
                border-radius: 12px;
                overflow: hidden;
                box-shadow: 0 4px 12px rgba(0,0,0,0.08);
            }
            .card-header {
                border-radius: 12px 12px 0 0 !important;
            }
            code {
                background: #f0f0f0;
                padding: 2px 6px;
                border-radius: 4px;
                font-size: 0.85rem;
            }
        </style>
        """

        return html


