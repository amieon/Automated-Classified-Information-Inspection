# report_generator.py
from datetime import datetime
from typing import Dict, List, Any, Optional

class ReportGenerator:
    """生成涉密检查结果报告"""

    @staticmethod
    def generate(results: Dict[str, List[Dict]]) -> str:
        """
        根据检查结果字典生成最终报告

        :param results: 格式如下：
            {
                "web": [
                    {
                        "source": "http://example.com",
                        "matches": [
                            {"line": 10, "keyword": "机密", "content": "这里是机密内容", "matched_text": "机密"}
                        ]
                    },
                    ...
                ],
                "file": [
                    {
                        "source": "/path/to/file.txt",
                        "matches": [
                            {"line": 5, "keyword": "秘密", "content": "内部资料", "matched_text": "秘密"}
                        ]
                    },
                    ...
                ],
                "image": [
                    {
                        "source": "/path/to/image.jpg",
                        "ocr_text": "...",
                        "matches": [
                            {"line": 1, "keyword": "保密", "content": "...", "matched_text": "保密"}
                        ]
                    },
                    ...
                ],
                "audio": [
                    {
                        "source": "/path/to/audio.wav",
                        "transcript": "...",
                        "matches": [
                            {"line": 1, "keyword": "密级", "content": "...", "matched_text": "密级"}
                        ]
                    },
                    ...
                ],
                "db": [
                    {
                        "source": "mysql://localhost:3306/mydb",
                        "tables": [
                            {
                                "table": "users",
                                "total_rows": 1000,
                                "sensitive_rows": 5,
                                "sensitive_fields": [
                                    {"field": "note", "samples": ["机密内容1", "机密内容2"]}
                                ]
                            },
                            {
                                "table": "documents",
                                "total_rows": 500,
                                "sensitive_rows": 2,
                                "sensitive_fields": [
                                    {"field": "title", "samples": ["绝密文件"]}
                                ]
                            }
                        ]
                    }
                ]
            }
        :return: 格式化后的报告字符串
        """
        lines = []
        lines.append("=" * 70)
        lines.append("        涉密信息检查报告")
        lines.append("=" * 70)
        lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        # 统计总数
        total_sources = 0
        total_matches = 0
        type_stats = {}

        for check_type, items in results.items():
            if not items:
                continue
            type_stats[check_type] = {
                "sources": len(items),
                "match_count": 0
            }
            for item in items:
                total_sources += 1
                matches = item.get("matches", [])
                if check_type == "db":
                    # 数据库单独统计
                    tables = item.get("tables", [])
                    for table in tables:
                        total_matches += table.get("sensitive_rows", 0)
                        type_stats[check_type]["match_count"] += table.get("sensitive_rows", 0)
                else:
                    total_matches += len(matches)
                    type_stats[check_type]["match_count"] += len(matches)

        # ---- 1. 总体摘要 ----
        lines.append("【一、总览】")
        lines.append(f"  检查类型：{', '.join(k for k, v in results.items() if v)}")
        lines.append(f"  检查目标总数：{total_sources}")
        lines.append(f"  发现涉密信息条目总数：{total_matches}")
        lines.append("")
        lines.append("  ┌───────────────────────┬───────────┬───────────┐")
        lines.append("  │ 类型                  │ 目标数量  │ 涉密条目  │")
        lines.append("  ├───────────────────────┼───────────┼───────────┤")
        for check_type, stat in type_stats.items():
            type_label = {
                "web": "网页",
                "file": "文件",
                "image": "图片",
                "audio": "音频",
                "db": "数据库"
            }.get(check_type, check_type)
            lines.append(f"  │ {type_label:<21} │ {stat['sources']:<9} │ {stat['match_count']:<9} │")
        lines.append("  └───────────────────────┴───────────┴───────────┘")
        lines.append("")

        # ---- 2. 各类型详细报告 ----
        lines.append("【二、详细结果】")
        lines.append("")

        for check_type, items in results.items():
            if not items:
                continue
            type_label = {
                "web": "🌐 网页",
                "file": "📄 文件",
                "image": "🖼️ 图片",
                "audio": "🎵 音频",
                "db": "🗄️ 数据库"
            }.get(check_type, check_type)

            lines.append(f"  ── {type_label} ──")
            lines.append("")

            for idx, item in enumerate(items, 1):
                source = item["source"]
                lines.append(f"    {idx}. 来源：{source}")

                if check_type == "db":
                    tables = item.get("tables", [])
                    if not tables:
                        lines.append("      无涉密表")
                    else:
                        lines.append(f"      共检查 {len(tables)} 个表：")
                        for table in tables:
                            sensitive_fields = table.get("sensitive_fields", [])
                            lines.append(f"        • 表 {table['table']}：总计 {table['total_rows']} 行，涉密 {table['sensitive_rows']} 行")
                            if sensitive_fields:
                                for sf in sensitive_fields:
                                    samples = sf.get("samples", [])
                                    sample_str = "；".join(samples[:3])  # 最多显示3个样例
                                    lines.append(f"          字段 '{sf['field']}' 包含涉密文本（例如：{sample_str}）")
                else:
                    matches = item.get("matches", [])
                    if not matches:
                        lines.append("      无涉密内容")
                    else:
                        lines.append(f"      发现 {len(matches)} 处涉密信息：")
                        for m in matches:
                            line = m.get("line", "?")
                            keyword = m.get("keyword", "")
                            content = m.get("content", "")
                            matched = m.get("matched_text", "")
                            lines.append(f"        → 第 {line} 行：关键词“{keyword}”，匹配内容“{matched}”")
                            if content:
                                lines.append(f"          原文：{content[:80]}{'...' if len(content)>80 else ''}")
                lines.append("")  # 空行分隔

        # ---- 3. 结论与建议 ----
        lines.append("【三、结论与建议】")
        if total_matches == 0:
            lines.append("   ✅ 未发现涉密信息，安全可靠。")
        else:
            lines.append(f"   ⚠️ 共发现 {total_matches} 处涉密信息，请立即核查并处理。")
            lines.append("   建议：")
            lines.append("     1. 对涉密文件/数据进行脱敏或隔离；")
            lines.append("     2. 修改相关权限设置，限制访问；")
            lines.append("     3. 提交审计追踪，追究责任。")
        lines.append("")
        lines.append("=" * 70)
        lines.append("报告结束")
        lines.append("=" * 70)

        return "\n".join(lines)

