from typing import List, Dict, Optional
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from pathlib import Path
from .base_checker import BaseChecker
from detector.leak_detector import LeakDetector
from utils.parallel import run_parallel
from utils.report_exporter import publish_latest_report


# ==================== 数据库连接器（不变） ====================

class DBConnector:
    """每个实例对应一条独立连接，线程间不共享"""

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
        try:
            if self.db_type == "mysql":
                import pymysql
                self.conn = pymysql.connect(
                    host=self.host, port=self.port,
                    user=self.user, password=self.password,
                    database=self.database, charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor
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
        if self.cursor:
            try: self.cursor.close()
            except: pass
        if self.conn:
            try: self.conn.close()
            except: pass

    def get_tables(self) -> List[str]:
        tables = []
        try:
            if self.db_type == "mysql":
                sql = "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME"
                self.cursor.execute(sql, (self.database,))
                for row in self.cursor.fetchall():
                    tables.append(row['TABLE_NAME'] if isinstance(row, dict) else row[0])
            elif self.db_type == "sqlite":
                self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                tables = [row[0] for row in self.cursor.fetchall()]
        except Exception as e:
            import traceback
            print(f"⚠️ 获取表名失败: {e}")
            traceback.print_exc()
        return tables

    def get_columns(self, table_name: str) -> List[Dict]:
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

    @staticmethod
    def is_text_column(col_type: str) -> bool:
        if not col_type:
            return False
        col_type = col_type.lower()
        text_types = ['char', 'varchar', 'text', 'tinytext', 'mediumtext', 'longtext',
                      'blob', 'mediumblob', 'longblob', 'json', 'enum', 'set']
        return any(col_type.startswith(t) for t in text_types)

    def get_primary_key(self, table_name: str) -> Optional[str]:
        try:
            columns = self.get_columns(table_name)
            for col in columns:
                if col.get("Key", "") == "PRI":
                    return col.get("Field", "")
        except:
            pass
        return None

    def search_in_table(self, table_name: str, detector: LeakDetector) -> List[Dict]:
        """扫描单张表的所有文本列，返回涉密行列表"""
        results = []
        columns = self.get_columns(table_name)
        text_columns = [col.get("Field", "") for col in columns
                        if self.is_text_column(col.get("Type", ""))]
        if not text_columns:
            return results

        pk = self.get_primary_key(table_name)
        col_str = ", ".join([f"`{c}`" for c in text_columns])
        select_str = f"`{pk}`, {col_str}" if pk else col_str

        batch_size = 500
        offset = 0

        try:
            while True:
                sql = f"SELECT {select_str} FROM `{table_name}` LIMIT {batch_size} OFFSET {offset}"
                self.cursor.execute(sql)
                rows = self.cursor.fetchall()
                if not rows:
                    break

                for row in rows:
                    for col_name in text_columns:
                        value = row.get(col_name)
                        if value is None or value == "":
                            continue
                        content = str(value)
                        leak_lines = detector.check_text(content)
                        if leak_lines:
                            for line_no, keyword, matched_content in leak_lines:
                                results.append({
                                    "table": table_name,
                                    "row_pk": str(row.get(pk, "N/A")) if pk else f"offset_{offset}",
                                    "column": col_name,
                                    "keyword": keyword,
                                    "content": matched_content[:200]
                                })
                offset += batch_size
                if len(rows) < batch_size:
                    break
        except Exception as e:
            print(f"⚠️ 搜索 {table_name} 失败: {e}")

        return results


# ==================== 数据库检查器模块 ====================

class DBCheckerModule(BaseChecker):
    DB_CONFIG_TEMPLATE = {
        "mysql": {"host": "localhost", "port": 3306, "user": "root", "password": "", "database": "your_database"},
        "sqlite": {"path": "/path/to/database.db"}
    }

    def register_routes(self, app: FastAPI):

        # ------ 通过连接参数检查 ------
        @app.post("/check/db/connect", response_class=HTMLResponse)
        async def check_db_connect(
                db_type: str = Form("mysql"),
                host: str = Form("localhost"),
                port: int = Form(3306),
                user: str = Form(""),
                password: str = Form(""),
                database: str = Form(""),
                dbname: str = Form(""),
                algorithm: str = Form("regex"),
                keywords: str = Form("秘密,机密,绝密,内部,涉密,保密,密级,不予公开"),
                max_insert: int = Form(3)
        ):
            # 先用一个临时连接取表名
            connector = DBConnector(db_type=db_type, host=host, port=port,
                                    user=user, password=password,
                                    database=database, dbname=dbname)
            if not connector.connect():
                return HTMLResponse(content="""
                <div class='alert alert-danger'>❌ 数据库连接失败！请检查连接参数。</div>
                """)

            try:
                tables = connector.get_tables()
                if not tables:
                    return HTMLResponse(content="""
                    <div class='alert alert-warning'>数据库连接成功，但未找到任何表。</div>
                    """)

                # ★ 并行扫描，直接传 form 参数
                all_results = self._parallel_scan(
                    tables=tables,
                    keywords=keywords,
                    algorithm=algorithm,
                    max_insert=max_insert,
                    db_type=db_type, host=host, port=port,
                    user=user, password=password,
                    database=database or dbname
                )

                db_info = f"MySQL {host}:{port}/{database or dbname}"
                html = self._build_html_result(all_results, tables, len(tables))
                text_report = self._generate_text_report(all_results, tables, len(tables), db_info)
                self._write_report(text_report)
                return HTMLResponse(content=html)
            finally:
                connector.disconnect()

        # ------ 通过 SQLite 路径检查 ------
        @app.post("/check/db/path", response_class=HTMLResponse)
        async def check_db_path(
                path: str = Form(...),
                algorithm: str = Form("regex"),
                keywords: str = Form("秘密,机密,绝密,内部,涉密,保密,密级,不予公开"),
                max_insert: int = Form(3)
        ):
            p = Path(path)
            if not p.exists():
                return HTMLResponse(content=f"<div class='alert alert-danger'>路径不存在: {path}</div>")
            if p.suffix.lower() not in ['.db', '.sqlite', '.sqlite3', '.db3']:
                return HTMLResponse(content=f"<div class='alert alert-warning'>文件 {p.name} 不是SQLite数据库文件</div>")

            connector = DBConnector(db_type="sqlite", database=str(p))
            if not connector.connect():
                return HTMLResponse(content="<div class='alert alert-danger'>❌ SQLite数据库打开失败！</div>")

            try:
                tables = connector.get_tables()
                if not tables:
                    return HTMLResponse(content="""
                    <div class='alert alert-warning'>数据库打开成功，但未找到任何表。</div>
                    """)

                all_results = self._parallel_scan(
                    tables=tables,
                    keywords=keywords,
                    algorithm=algorithm,
                    max_insert=max_insert,
                    db_type="sqlite",
                    database=str(p)
                )

                html = self._build_html_result(all_results, tables, len(tables), db_path=str(p))
                text_report = self._generate_text_report(all_results, tables, len(tables),
                                                         f"SQLite 文件: {p}")
                self._write_report(text_report)
                return HTMLResponse(content=html)
            finally:
                connector.disconnect()

    # ==================== 核心：并行表扫描 ====================
    @staticmethod
    def _scan_single_table(args) -> List[Dict]:
        """
        在子线程中运行：创建独立连接 → 扫描单张表 → 断开。
        args = (conn_kwargs, table_name, detector_kwargs)
        """
        conn_kwargs, table_name, detector_kwargs = args

        connector = DBConnector(**conn_kwargs)
        if not connector.connect():
            return []

        try:
            detector = LeakDetector(**detector_kwargs)
            return connector.search_in_table(table_name, detector)
        finally:
            connector.disconnect()

    def _parallel_scan(
            self,
            tables: List[str],
            keywords: str,
            algorithm: str,
            max_insert: int,
            **conn_kwargs
    ) -> List[Dict]:
        """
        使用线程池并行扫描所有表。
        conn_kwargs: db_type, host, port, user, password, database 等
        """
        if not tables:
            return []

        # ★ 直接用 form 参数构建 detector_kwargs，不再从实例提取
        detector_kwargs = {
            "keywords": keywords,
            "algorithm": algorithm,
            "max_insert": max_insert
        }

        tasks = [(conn_kwargs, table_name, detector_kwargs) for table_name in tables]

        results_per_table = run_parallel(
            process_func=self._scan_single_table,
            items=tasks,
            max_workers=4,
            executor_type="thread",
            description="扫描数据库表中"
        )

        # 汇总
        all_results = []
        for table_results in results_per_table:
            if table_results:
                all_results.extend(table_results)
        return all_results

    # ==================== 辅助方法 ====================
    @staticmethod
    def _write_report(text_report: str):
        publish_latest_report(text_report)

    @staticmethod
    def _generate_text_report(results: list, all_tables: list,
                              searched_tables: int, db_info: str) -> str:
        from datetime import datetime
        lines = [
            "=" * 60,
            "          数据库涉密数据检查报告",
            "=" * 60,
            f"数据库信息: {db_info}",
            f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"扫描表数: {searched_tables}",
            f"涉密数据条数: {len(results)}",
            f"涉及表数: {len(set(r['table'] for r in results))}",
            "-" * 60
        ]
        if not results:
            lines.append("未发现涉密数据。")
        else:
            table_groups: Dict[str, list] = {}
            for r in results:
                table_groups.setdefault(r['table'], []).append(r)
            lines.append("详细结果：")
            for table_name, items in table_groups.items():
                lines.append(f"\n【表名】{table_name} (共 {len(items)} 条)")
                for item in items:
                    lines.append(
                        f"  行 {item.get('row_pk','N/A')} | "
                        f"字段 {item.get('column','')} | "
                        f"关键词 [{item.get('keyword','')}] → "
                        f"{item.get('content','')}"
                    )
        lines.append("=" * 60)
        lines.append("报告结束")
        return "\n".join(lines)

    @staticmethod
    def _build_html_result(results: list, all_tables: list,
                           searched_tables: int, db_path: str = "") -> str:
        import html as html_mod

        total_tables = len(all_tables)
        leak_count = len(results)
        affected_tables = len(set(r['table'] for r in results))

        table_groups: Dict[str, list] = {}
        for r in results:
            table_groups.setdefault(r['table'], []).append(r)

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
            html += '<div class="alert alert-success">✅ 未发现涉密数据！</div>'
            return html

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
                        <tbody>"""

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
                    <td><pre style="white-space:pre-wrap;word-wrap:break-word;max-height:100px;overflow-y:auto;margin:0;font-size:0.85rem;">{content}</pre></td>
                </tr>"""

            html += """
                        </tbody>
                    </table>
                </div>
            </div>"""

        html += f"""
        <div class="card mt-3">
            <div class="card-header"><strong>📊 统计总览</strong></div>
            <div class="card-body">
                <table class="table table-sm table-bordered">
                    <tr><th>数据库表总数</th><td>{total_tables}</td></tr>
                    <tr><th>已扫描表数</th><td>{searched_tables}</td></tr>
                    <tr><th>涉密数据条数</th><td><span class="highlight-badge">{leak_count}</span></td></tr>
                    <tr><th>涉及表数</th><td>{affected_tables}</td></tr>
                </table>
            </div>
        </div>
        <style>
            .result-table th {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white; font-weight: 600;
            }}
            .highlight-badge {{
                background: #ff6b6b; color: white;
                padding: 4px 12px; border-radius: 20px;
                font-size: 0.9rem; display: inline-block;
            }}
            .card {{
                border-radius: 12px; overflow: hidden;
                box-shadow: 0 4px 12px rgba(0,0,0,0.08);
            }}
            .card-header {{ border-radius: 12px 12px 0 0 !important; }}
            code {{
                background: #f0f0f0; padding: 2px 6px;
                border-radius: 4px; font-size: 0.85rem;
            }}
        </style>
        """
        return html
