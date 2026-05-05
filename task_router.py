#!/usr/bin/env python3
"""
TaskRouter — 任务路由决策引擎
================================

整合自 Brain-v1.1.8 subagent-think-chain.js

在派发 subagent 前，必须运行此决策引擎。

决策输出（JSON）：
  {
    "task": str,
    "category": str,           # search/code/write/reason/memory/browser/config
    "confidence": float,       # 0.0-1.0
    "level": str,              # high/medium/low
    "should_split": bool,
    "split_reason": str,
    "agents": [{"direction": str, "model": str, "prompt": str}],
    "need_verification": bool,
    "selected_model": str,
    "warnings": [str],
  }

使用方式：
  python3 task_router.py "调研推特AI博主最新趋势"
  python3 task_router.py "修复量化回测的bug" --json
"""
import re
import json
import datetime
from typing import Optional

# ─────────────────────────────────────────────────────────────
# 信号表（Brain v1.1.8）
# ─────────────────────────────────────────────────────────────

SIGNAL_TABLE = [
    # 不可逆操作
    (r'删除|销毁|rm\s|drop\s|remove.*永久', -0.3, '不可逆操作'),
    # 对外操作
    (r'发布|上线|deploy|推送|发送.*邮件|发.*消息', -0.2, '对外操作，后果严重'),
    # 系统核心修改
    (r'配置.*网关|gateway.*配置|restart.*网关|内核|kernel', -0.2, '系统核心修改'),
    # 安全相关
    (r'密码|密钥|secret|credential|api.?key|私钥', -0.2, '安全相关'),
    # 子任务过多
    (r'子任务.*[>2]|多个.*并行', -0.1, '子任务>2，链路长易漂移'),
    # 首次遇到
    (r'首次|没做过|不熟悉|从来没', -0.2, '首次遇到，无先例可循'),
    # 历史成功率高
    (r'之前成功|做过.*成功|之前.*成功|历史成功|proven', +0.1, '历史成功率高'),
    # 任务明确简单
    (r'简单|直接|就行|一下|quick|easy', +0.1, '任务明确简单'),
    # 不确定性关键词
    (r'试试|可能|看看|maybe|perhaps', -0.1, '包含不确定性关键词'),
    # 架构/重构
    (r'架构|重构|系统设计', -0.1, '涉及架构重构'),
]

# 需要对抗验证的信号
VERIFICATION_SIGNALS = [
    (r'架构|重构|系统设计', '涉及核心架构'),
    (r'安全|权限', '安全相关'),
    (r'方案|策略|计划.*实施', '重要决策'),
]

# 模型路由规则
MODEL_ROUTING = [
    {'categories': ['search'], 'model': 'MiniMax-M2.7', 'reason': '搜索调研类任务'},
    {'categories': ['code'],   'model': 'MiniMax-M2.7', 'reason': '代码类任务'},
    {'categories': ['write'],  'model': 'MiniMax-M2.7', 'reason': '文案写作类任务'},
    {'categories': ['reason'], 'model': 'MiniMax-M2.7', 'reason': '推理分析类任务'},
]

DEFAULT_MODEL = 'MiniMax-M2.7'

# 任务类型检测
CATEGORY_PATTERNS = {
    'search':   ['搜索', '调研', '查找', '搜', '研究', '查'],
    'code':     ['代码', '改bug', '调试', 'debug', '脚本', '编程', '写代码'],
    'write':    ['写', '文案', '文章', '内容', '生成', '创作', '润色'],
    'reason':   ['分析', '推理', '判断', '评估', '诊断', '排查'],
    'memory':   ['记忆', '日志', '记录', '存档', '整理记忆'],
    'browser':  ['浏览器', '网页', '网站'],
    'config':   ['配置', 'config', '模型', '网关', 'gateway', 'openclaw'],
}

# 拆分信号（保守策略，避免过度拆分）
SPLIT_SIGNALS = [
    (r'对比.*多个|比较.*之间', '明确对比分析需求'),
    (r'分别调研|逐一|各自', '明确分离处理需求'),
]

NO_SPLIT_SIGNALS = [
    (r'直接|就行|简单|一下|就这样', '用户要求直接做，不拆分'),
    (r'^读.*文件$|^查看.*$|总结|汇总', '单一目标，不需要拆分'),
    (r'只|只要|仅', '目标单一，不需要拆分'),
]


# ─────────────────────────────────────────────────────────────
# 核心决策函数
# ─────────────────────────────────────────────────────────────

def classify_task(task: str) -> list[str]:
    """检测任务类别"""
    task_lower = task.lower()
    detected = []
    for cat, keywords in CATEGORY_PATTERNS.items():
        if any(k in task_lower for k in keywords):
            detected.append(cat)
    return detected if detected else ['general']


def assess_confidence(task: str, is_config: bool = False) -> dict:
    """
    置信度评估

    初始值：
      配置类任务 0.7
      其他任务 0.9

    返回：{"score": float, "signals": [(matched_text, delta, reason)], "level": str}
    """
    score = 0.7 if is_config else 0.9
    matched_signals = []

    for pattern, delta, reason in SIGNAL_TABLE:
        if re.search(pattern, task, re.IGNORECASE):
            score += delta
            matched_signals.append({
                'matched': re.findall(pattern, task, re.IGNORECASE)[0],
                'delta': delta,
                'reason': reason
            })

    # 长度惩罚
    if len(task) > 200:
        score -= 0.1
        matched_signals.append({
            'matched': f'({len(task)}字符)',
            'delta': -0.1,
            'reason': '任务描述过长，可能复杂'
        })

    score = max(0.0, min(1.0, score))

    if score >= 0.7:
        level = 'high'
    elif score >= 0.4:
        level = 'medium'
    else:
        level = 'low'

    return {
        'score': round(score, 2),
        'signals': matched_signals,
        'level': level
    }


def check_verification(task: str) -> tuple[bool, str]:
    """检查是否需要对抗验证"""
    for pattern, reason in VERIFICATION_SIGNALS:
        if re.search(pattern, task):
            return True, reason
    return False, ''


def decide_split(task: str) -> tuple[bool, str]:
    """
    决定是否拆分任务（保守策略）
    NO_SPLIT 信号优先级最高
    """
    task_lower = task.lower()

    for pattern, reason in NO_SPLIT_SIGNALS:
        if re.search(pattern, task_lower):
            return False, reason

    for pattern, reason in SPLIT_SIGNALS:
        if re.search(pattern, task_lower):
            return True, reason

    # 任务描述过长时保守拆分
    if len(task) > 100 and not any(k in task_lower for k in ['直接', '简单', '就行']):
        return True, '任务描述较长，可能涉及多个方向'

    return False, '无拆分必要'


def select_model(categories: list[str]) -> tuple[str, str]:
    """根据任务类别选择模型"""
    for rule in MODEL_ROUTING:
        if any(cat in categories for cat in rule['categories']):
            return rule['model'], rule['reason']
    return DEFAULT_MODEL, '默认模型'


def generate_agents(task: str, should_split: bool, model: str) -> list[dict]:
    """
    生成 subagent 列表
    最多 3 个（对应对象A/B/C）
    """
    if not should_split:
        return [{
            'direction': '单一方向',
            'model': model,
            'prompt': task
        }]

    # 计算 agent 数量
    # "A 和 B 对比" → 2个；"A 和 B 和 C" → 3个
    and_count = len(re.findall(r'\b和\b', task))
    agent_count = min(and_count + 1, 3)

    directions = ['对象A', '对象B', '对象C']
    agents = []
    for i in range(agent_count):
        agents.append({
            'direction': directions[i],
            'model': model,
            'prompt': f"{task} - 重点：{directions[i]}"
        })
    return agents


def route_task(task: str) -> dict:
    """
    完整路由决策

    返回：
      {
        "task": str,
        "category": str,
        "confidence": float,
        "confidence_level": str,
        "confidence_signals": [str],
        "should_split": bool,
        "split_reason": str,
        "agents": [...],
        "need_verification": bool,
        "verification_reason": str,
        "selected_model": str,
        "model_reason": str,
        "warnings": [str],
      }
    """
    # 1. 类别检测
    categories = classify_task(task)
    is_config = 'config' in categories

    # 2. 置信度
    conf = assess_confidence(task, is_config)

    # 3. 是否需要验证
    need_verify, verify_reason = check_verification(task)
    if conf['score'] < 0.6 and not need_verify:
        need_verify = True
        verify_reason = '置信度低于0.6，需要验证'

    # 4. 是否拆分
    should_split, split_reason = decide_split(task)

    # 5. 模型选择
    selected_model, model_reason = select_model(categories)

    # 6. 生成 agents
    agents = generate_agents(task, should_split, selected_model)

    # 7. 生成 warnings
    warnings = []
    if conf['score'] < 0.6:
        warnings.append(f'⚠️ 置信度 {conf["score"]} 低于 0.6，建议谨慎操作')
    if need_verify:
        warnings.append(f'🔍 需要验证: {verify_reason}')
    if should_split:
        warnings.append(f'🔀 拆分为 {len(agents)} 个并行 agent')
    if is_config:
        warnings.append('⚙️ 配置类任务：执行前必须验证路径存在，执行后必须输出验证结果')

    # 8. 信号列表（用于展示）
    signal_strs = [f"[{s['delta']:+.1f}] {s['reason']} ({s['matched']})" for s in conf['signals']]

    return {
        'task':              task,
        'timestamp':         datetime.datetime.now().isoformat(),
        'category':          categories[0] if categories else 'general',
        'confidence':        conf['score'],
        'confidence_level':  conf['level'],
        'confidence_signals': signal_strs,
        'should_split':      should_split,
        'split_reason':      split_reason,
        'agents':           agents,
        'need_verification': need_verify,
        'verification_reason': verify_reason,
        'selected_model':   selected_model,
        'model_reason':     model_reason,
        'warnings':         warnings,
    }


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='TaskRouter 任务路由决策引擎')
    parser.add_argument('task', nargs='*', help='任务描述')
    parser.add_argument('--json', '-j', action='store_true', help='JSON 输出')
    parser.add_argument('--simple', '-s', action='store_true', help='简化输出')

    args = parser.parse_args()

    task = ' '.join(args.task) if args.task else input('请输入任务描述: ')
    if not task.strip():
        print("❌ 任务描述不能为空")
        exit(1)

    decision = route_task(task)

    if args.json:
        print(json.dumps(decision, ensure_ascii=False, indent=2))
    elif args.simple:
        conf_icon = {'high': '✅', 'medium': '⚠️', 'low': '🚨'}
        icon = conf_icon.get(decision['confidence_level'], '❓')
        print(f"{icon} 置信度: {decision['confidence']} ({decision['confidence_level']})")
        print(f"{'🔀 拆分' if decision['should_split'] else '➡️ 单一'}: {decision['split_reason']}")
        print(f"🤖 模型: {decision['selected_model']} ({decision['model_reason']})")
        if decision['warnings']:
            print(f"⚠️ 警告:")
            for w in decision['warnings']:
                print(f"   {w}")
    else:
        print(f"""
📊 任务路由决策
════════════════════════════════════════════
任务: {decision['task'][:60]}{'...' if len(decision['task']) > 60 else ''}

🔍 置信度: {decision['confidence']} ({decision['confidence_level']})
   信号: {', '.join(decision['confidence_signals']) if decision['confidence_signals'] else '无'}

🔀 拆分: {'是' if decision['should_split'] else '否'} → {decision['split_reason']}

🤖 模型: {decision['selected_model']} ({decision['model_reason']})
📂 类别: {decision['category']}

🔧 Agent 数量: {len(decision['agents'])}
""")
        if decision['agents']:
            for i, agent in enumerate(decision['agents']):
                print(f"   Agent {i+1}: [{agent['direction']}] {agent['model']}")
                print(f"     → {agent['prompt'][:80]}{'...' if len(agent['prompt']) > 80 else ''}")
                print()

        if decision['warnings']:
            print("⚠️ 警告:")
            for w in decision['warnings']:
                print(f"   {w}")
