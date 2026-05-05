#!/usr/bin/env python3
"""
记忆进化触发器 - 完整的自我进化流程
分析近期记忆，提炼规则，检测进化机会
支持 --apply 将验证规则自动写入 SOUL.md
"""
import sys, argparse, re
from pathlib import Path

from memory_consts import WORKSPACE, MEMORY_DIR, SKILL_DIR
sys.path.insert(0, str(SKILL_DIR))


# ─────────────────────────────────────────────────────────────
# 健壮 entry 解析
# ─────────────────────────────────────────────────────────────

def _parse_single_entry(text):
    """解析单个 entry 文本（已按 entry 分割）
    兼容格式：
      格式A: ---front_matter--- body [---]
      格式B: front_matter_field (无前导 ---)
      格式C: 纯 body
    """
    text = text.strip()
    if not text:
        return None

    # 格式B/C：没有前导 ---
    if not text.startswith('---'):
        first_line = text.split('\n')[0]
        if ':' in first_line and len(first_line.split(':')[0].strip()) < 20:
            # 可能是 front matter 但开头的 --- 被分割掉了
            cm = text.find('\n---\n')
            if cm > 0:
                fm_t = text[:cm + 5]
                body_t = text[cm + 5:].strip()
                meta = {}
                for ln in fm_t.split('\n'):
                    if ':' in ln:
                        k, v = ln.split(':', 1)
                        meta[k.strip()] = v.strip()
                body_t = re.sub(r'\n?-{3,}$', '', body_t).strip()
                return {'meta': meta, 'body': body_t}
        return None

    # 格式A：有前导 ---
    positions = [m.start() for m in re.finditer(r'\n---', text)]
    if not positions:
        return None

    # front matter closer：第一段 \n--- 后面不是另一个 ---
    fm_cp = None
    for pos in positions:
        remaining = text[pos + 4:]
        if not remaining.startswith('---'):
            fm_cp = pos
            break
    if fm_cp is None:
        return None

    # body closer：\n--- 后面紧跟另一个 \n---（下一 entry）
    body_cp = None
    for pos in positions:
        if pos <= fm_cp:
            continue
        if text[pos + 4:].startswith('---'):
            body_cp = pos
            break

    if body_cp is not None:
        body = text[fm_cp + 4:body_cp].strip()
    else:
        body = text[fm_cp + 4:].strip()

    # 去除末尾的 --- 行
    body = re.sub(r'\n?-{3,}$', '', body).strip()

    fm_t = text[:fm_cp + 4]
    meta = {}
    for ln in fm_t.split('\n')[1:]:
        if ':' in ln:
            k, v = ln.split(':', 1)
            meta[k.strip()] = v.strip()
    return {'meta': meta, 'body': body}


def _split_entries(raw):
    """将文件 raw 文本拆分为多个 entry"""
    results = []
    # 按 entry 边界分割（空白行 + 连字符簇）
    parts = re.split(r'\n\n-{3,}\n', raw)
    for part in parts:
        part = part.rstrip()
        if not part:
            continue
        if not part.startswith('---'):
            # 在 body closer 处分割：\n--- 后面紧跟另一个 \n---
            sub_parts = re.split(r'\n---\n(?=---)', part)
            for sp in sub_parts:
                sp = sp.strip()
                if sp:
                    result = _parse_single_entry(sp)
                    if result:
                        results.append(result)
        else:
            result = _parse_single_entry(part)
            if result:
                results.append(result)
    return results


# ─────────────────────────────────────────────────────────────
# 核心分析函数
# ─────────────────────────────────────────────────────────────

def load_recent_memories(days=7):
    """加载近N天的记忆，按 entry 拆分"""
    from datetime import datetime, timedelta
    memories = []
    cutoff = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff.strftime('%Y-%m-%d')

    for memory_type in ['error', 'correction', 'practice', 'event', 'gap']:
        type_dir = MEMORY_DIR / memory_type
        if not type_dir.exists():
            continue

        for md_file in sorted(type_dir.glob("*.md")):
            if md_file.stem >= cutoff_str:
                try:
                    with open(md_file, 'r', encoding='utf-8') as f:
                        raw = f.read()
                    entries = _split_entries(raw)
                    for entry in entries:
                        memories.append({
                            'type': memory_type,
                            'file': md_file.name,
                            'body': entry['body'],
                            'meta': entry['meta'],
                            'date': md_file.stem
                        })
                except Exception:
                    pass
    return memories


def analyze_corrections(memories):
    """提取纠正规则，并按 (wrong, correct) 去重"""
    seen = set()
    rules = []
    for m in [x for x in memories if x['type'] == 'correction']:
        wrong, correct = '', ''
        for line in m['body'].split('\n'):
            if '错误做法:' in line:
                wrong = line.split('错误做法:', 1)[1].strip()
            if '正确做法:' in line:
                correct = line.split('正确做法:', 1)[1].strip()
        if wrong or correct:
            key = (wrong, correct)
            if key not in seen:
                seen.add(key)
                rules.append({
                    'wrong': wrong, 'correct': correct,
                    'source': m['file'], 'date': m['date']
                })
    return rules


def analyze_errors(memories):
    """提取错误模式，并按 error 文本去重"""
    seen = set()
    patterns = []
    for m in [x for x in memories if x['type'] == 'error']:
        error, fix = '', ''
        for line in m['body'].split('\n'):
            if '错误:' in line:
                error = line.split('错误:', 1)[1].strip()
            if '建议:' in line:
                fix = line.split('建议:', 1)[1].strip()
        if error and error not in seen:
            seen.add(error)
            patterns.append({
                'error': error, 'fix': fix,
                'source': m['file'], 'date': m['date']
            })
    return patterns


def analyze_practices(memories):
    seen = set()
    practices = []
    for m in memories:
        if m['type'] != 'practice':
            continue
        content = m['body'].strip()
        if content and content not in seen:
            seen.add(content)
            practices.append({
                'content': content,
                'source': m.get('source', m['file']),
                'date': m['date']
            })
    return practices


def detect_evolution_opportunities(rules, errors, practices):
    opportunities = []

    # 纠正规则验证N次
    rule_count = {}
    for r in rules:
        if r['correct']:
            rule_count[r['correct']] = rule_count.get(r['correct'], 0) + 1
    for correct, count in rule_count.items():
        if count >= 3:
            opportunities.append({
                'kind': 'RULE_PROMOTION',
                'content': correct,
                'count': count,
                'action': f'纠正验证{count}次，可写入 SOUL.md'
            })

    # 错误模式重复
    error_patterns = {}
    for e in errors:
        if e['fix']:
            error_patterns[e['fix']] = error_patterns.get(e['fix'], 0) + 1
    for fix, count in error_patterns.items():
        if count >= 2:
            opportunities.append({
                'kind': 'ERROR_PATTERN',
                'content': fix,
                'count': count,
                'action': f'错误重复{count}次，建议固化为预防规则'
            })

    # 最佳实践
    for p in practices:
        if any(kw in p['content'] for kw in ['高效', '优化', '推荐', '最佳']):
            opportunities.append({
                'kind': 'PRACTICE',
                'content': p['content'][:100],
                'source': p.get('source', ''),
                'action': '可作为最佳实践推广'
            })

    return opportunities


def apply_promotions(opportunities, dry_run=False):
    """将验证规则写入 SOUL.md"""
    soul_path = WORKSPACE / 'SOUL.md'
    if not soul_path.exists():
        print(f"❌ SOUL.md 不存在: {soul_path}")
        return 0

    promotions = [
        o for o in opportunities
        if o['kind'] in ('RULE_PROMOTION', 'ERROR_PATTERN')
        and o.get('count', 0) >= 3
    ]

    if not promotions:
        print("  没有达到升级条件的规则（需≥3次验证）")
        return 0

    from datetime import datetime
    with open(soul_path, 'r', encoding='utf-8') as f:
        content_str = f.read()

    timestamp = datetime.now().strftime('%Y-%m-%d')
    added = 0
    new_entries = []

    # 提取 SOUL.md 中已有的规则内容（用于去重）
    existing_rules = set()
    import re
    for line in content_str.split('\n'):
        m = re.match(r'^-\s+\[[\d-]+\]\s+(.+)', line)
        if m:
            existing_rules.add(m.group(1).strip())

    for p in promotions:
        rule_content = p['content'].strip()
        if rule_content in existing_rules:
            continue  # 已存在，跳过
        rule_text = f"- [{timestamp}] {rule_content}"
        new_entries.append(rule_text)
        added += 1

    if added == 0:
        print("  所有规则已在 SOUL.md 中")
        return 0

    if dry_run:
        print(f"  [DRY RUN] 将在 SOUL.md 追加 {added} 条规则：")
        for r in new_entries:
            print(f"    + {r}")
        return added

    # 找到 SOUL.md 末尾（最后一个 `---` 分隔线之后）
    marker = "\n---\n"
    if marker in content_str:
        insert_pos = content_str.rfind(marker) + len(marker)
    else:
        insert_pos = len(content_str)
        if not content_str.endswith("\n"):
            content_str += "\n"
            insert_pos = len(content_str)

    new_section = "\n".join(new_entries) + "\n"
    updated = content_str[:insert_pos] + new_section + content_str[insert_pos:]

    with open(soul_path, 'w', encoding='utf-8') as f:
        f.write(updated)

    print(f"  ✅ 已将 {added} 条规则写入 SOUL.md")
    return added


def generate_report(days=7, apply=False, dry_run=False):
    from datetime import datetime
    print("🧬 记忆自我进化分析\n" + "=" * 60)
    print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"分析范围: 近{days}天记忆\n")

    memories = load_recent_memories(days=days)
    print(f"📊 记忆统计: 共加载 {len(memories)} 条记忆")
    for t in ['error', 'correction', 'practice', 'event', 'gap']:
        cnt = len([m for m in memories if m['type'] == t])
        if cnt:
            print(f"   {t}: {cnt} 条")

    rules = analyze_corrections(memories)
    errors = analyze_errors(memories)
    practices = analyze_practices(memories)
    opportunities = detect_evolution_opportunities(rules, errors, practices)

    print(f"\n🔬 进化机会检测: 发现 {len(opportunities)} 项")
    print("-" * 60)

    if opportunities:
        for i, opp in enumerate(opportunities, 1):
            print(f"\n【{i}】{opp['kind']}")
            print(f"   内容: {opp['content'][:100]}")
            if 'count' in opp:
                print(f"   次数: {opp['count']}次")
            print(f"   建议: {opp['action']}")
    else:
        print("   暂无明显进化机会，继续积累经验。")

    if rules:
        print(f"\n📝 纠正规则（共 {len(rules)} 条，去重后）:")
        for r in rules[:5]:
            print(f"   ✗ {r['wrong'][:40]} → ✓ {r['correct'][:40]} ({r['date']})")

    if errors:
        print(f"\n💥 错误模式（共 {len(errors)} 条，去重后）:")
        for e in errors[:5]:
            if e['fix']:
                print(f"   ⚠ {e['error'][:50]}")
                print(f"   💡 解决: {e['fix']}")

    if practices:
        print(f"\n✅ 最佳实践（共 {len(practices)} 条）:")
        for p in practices[:5]:
            content = p['content'][:80].replace('\n', ' ')
            print(f"   {content}")

    print("\n" + "=" * 60)

    applied = 0
    if apply:
        print("\n🚀 执行进化升级...")
        applied = apply_promotions(opportunities, dry_run=dry_run)
    else:
        print("💡 下次进化计划: 达到3次验证后可用 --apply 写入 SOUL.md")

    return {
        'memories_count': len(memories),
        'opportunities': opportunities,
        'rules': rules,
        'errors': errors,
        'practices': practices,
        'applied': applied
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='记忆自我进化分析')
    parser.add_argument('--days', type=int, default=7, help='分析近N天记忆（默认7）')
    parser.add_argument('--apply', action='store_true', help='将验证规则写入 SOUL.md')
    parser.add_argument('--dry-run', action='store_true', help='预览 --apply 效果，不实际写入')
    args = parser.parse_args()

    generate_report(days=args.days, apply=args.apply, dry_run=args.dry_run)
