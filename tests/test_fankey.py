# -*- coding: utf-8 -*-
"""屏幕提示文案的测试。

    runtime\\python.exe tests\\test_fankey.py

盯的是五件容易悄悄坏掉的事：
  * 全项目只有一套模式词（省电/均衡/流畅/性能），弹窗不许另造说法；
  * 三态文案不能张冠李戴（把强冷说成自动，用户会以为按键坏了）；
  * 实体键的三态映射到哪个控制意图（全亮→锁性能、半亮→自适应、不亮→锁省电）；
  * 风扇字节 → 硬件模式的映射表和 EC 通道共用一份，不许各存各的；
  * 认不出的取值照实说，不猜。
弹不弹的规矩（人动手才弹）在 daemon 里，这里只管文案和映射。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.policy.scheduler import INTENT_NAMES, TIER_LABELS     # noqa: E402
from app.tray import fankey                                 # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case('实体键全亮 → 性能模式，副标题说清功耗墙和风扇都变了')
def _():
    title, sub = fankey.key_text('Turbo_Mode')
    assert title == '性能模式', title
    assert '强冷' in sub and '75W' in sub, sub


@case('实体键半亮 → 自适应模式，副标题说清交回自动')
def _():
    title, sub = fankey.key_text('Normal_Mode')
    assert title == '自适应模式', title
    assert '自动' in sub, sub


@case('User_Fan 全家族 = 低功耗档：必须说省电模式和 10W，不许说「电源模式不变」')
def _():
    # 2026-09-30 全表差分抓到这一态 PL1_SETTING_VALUE 从 75 掉到 10，
    # 以前文案写「只接管风扇、电源不变」是错的，用户看到面板还显示自适应。
    for flag in ('User_Fan_Mode', 'User_Fan_HiMode', 'User_Fan_Level3'):
        title, sub = fankey.key_text(flag)
        assert title == '省电模式', (flag, title)
        assert '10W' in sub, (flag, sub)
        assert '电源模式不变' not in sub, (flag, sub)


@case('认不出的取值照实说，不猜')
def _():
    title, sub = fankey.key_text('Turbo_Mode+FanBoost_Mode')
    assert title == '风扇模式变了', title
    assert 'Turbo_Mode+FanBoost_Mode' in sub, sub
    title, sub = fankey.key_text(None)
    assert '未知' in sub, sub


@case('意图弹窗复用模式词：锁定性能 → 性能模式 + 不再自动切换')
def _():
    title, sub = fankey.intent_text('turbo')
    assert title == '性能模式', title
    assert '不再自动切换' in sub, sub
    title, sub = fankey.intent_text('office')
    assert title == '省电模式', title
    title, sub = fankey.intent_text('balance')
    assert title == '均衡模式', title


@case('意图自适应 → 自适应模式 + 自动换档')
def _():
    title, sub = fankey.intent_text('auto')
    assert title == '自适应模式', title
    assert '自动换档' in sub, sub


@case('意图取值不认识时不许编模式名')
def _():
    title, sub = fankey.intent_text('gaming')
    assert title == '控制意图变了', title
    assert 'gaming' in sub, sub


@case('风扇按钮弹窗：网页写的就是同一个字节，词必须和按键一致')
def _():
    for flag in ('Turbo_Mode', 'Normal_Mode', 'FanBoost_Mode', 'User_Fan_Mode'):
        assert fankey.fan_text(flag)[0] == fankey.key_text(flag)[0], flag


@case('调度性格弹窗带上性格名和说明')
def _():
    title, sub = fankey.profile_text('安静优先', '升档慢、降档快，兼顾噪音')
    assert title == '调度性格：安静优先', title
    assert sub == '升档慢、降档快，兼顾噪音', sub


@case('实体键三态 → 控制意图：全亮锁性能、半亮回自适应、不亮锁省电')
def _():
    assert fankey.key_intent('Turbo_Mode') == 'turbo'
    assert fankey.key_intent('Normal_Mode') == 'auto'
    # 不亮那一态是完整的低功耗档（PL1 10W），意图必须跟着落到省电，
    # 否则面板显示自适应、机器跑省电档，用户看到的和实际的对不上。
    for flag in ('User_Fan_Mode', 'User_Fan_HiMode', 'User_Fan_Level3'):
        assert fankey.key_intent(flag) == 'office', flag
    # 认不出的取值不敢猜：只弹提示，不动电源。
    for flag in ('FanBoost_Mode', 'Turbo_Mode+FanBoost_Mode', None):
        assert fankey.key_intent(flag) is None, flag


@case('风扇字节 → 硬件模式：映射表和 EC 通道共用一份')
def _():
    assert fankey.hw_mode('Turbo_Mode') == 'perf'
    assert fankey.hw_mode('Normal_Mode') == 'auto'
    assert fankey.hw_mode('User_Fan_HiMode') == 'eco'
    assert fankey.hw_mode('FanBoost_Mode') is None
    assert fankey.hw_mode(None) is None
    # 硬件模式只许用调度那套四档词，不许冒出第二套说法
    for flag in ('Turbo_Mode', 'Normal_Mode', 'User_Fan_HiMode'):
        m = fankey.hw_mode(flag)
        assert m == 'auto' or m in TIER_LABELS, (flag, m)


@case('按键弹的标题和意图弹的标题必须是同一句话（两套词就白对齐了）')
def _():
    pairs = [('Turbo_Mode', 'turbo'), ('Normal_Mode', 'auto'),
             ('User_Fan_HiMode', 'office')]
    for flag, intent in pairs:
        assert fankey.key_intent(flag) == intent, (flag, intent)
        assert intent in INTENT_NAMES, (flag, intent)
        assert fankey.key_text(flag)[0] == fankey.intent_text(intent)[0], flag


@case('网页词表来自后端：mode_words 覆盖按键三态 + User_Fan 家族')
def _():
    words = fankey.mode_words()
    for flag in ('Turbo_Mode', 'Normal_Mode', 'FanBoost_Mode'):
        assert words[flag] == fankey.key_text(flag)[0], flag
    assert words['User_Fan'] == fankey.USER_FAN_TEXT[0], words


@case('文案里不许再出现第二套模式词')
def _():
    texts = list(fankey.FAN_KEY_TEXT.values()) + list(fankey.FAN_FLAG_TEXT.values())
    texts += [fankey.USER_FAN_TEXT]
    texts += [fankey.intent_text(i) for i in ('auto', 'office', 'balance', 'turbo')]
    texts += [fankey.key_text(f) for f in ('Turbo_Mode', 'Normal_Mode', 'User_Fan_Mode', 'X')]
    for title, sub in texts:
        for banned in ('办公', '狂暴', '造物者 开', '造物者 关'):
            assert banned not in title + sub, (banned, title, sub)


def main():
    print('屏幕提示文案：%d 个场景' % len(CASES))
    bad = 0
    for i, (name, fn) in enumerate(CASES, 1):
        try:
            fn()
            print('  ok   [%02d] %s' % (i, name))
        except AssertionError as exc:
            bad += 1
            print('  FAIL [%02d] %s -> %s' % (i, name, exc))
        except Exception as exc:                            # noqa: BLE001
            bad += 1
            print('  ERR  [%02d] %s -> %r' % (i, name, exc))
    print('\n结果：%d/%d 通过' % (len(CASES) - bad, len(CASES)))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
