# -*- coding: utf-8 -*-
"""屏幕提示（OSD）的文案：全项目只有一套模式词。

词表在 app.policy.scheduler：省电/均衡/流畅/性能 四个模式，意图只是
「自适应 / 锁定某模式」。这里不再出现「办公」「狂暴」「造物者 开/关」
这类第二套说法——用户反馈过网页一套词、弹窗一套词，对不上号。

什么时候该弹、什么时候闭嘴（用户定的规矩）：
  * 人动手才弹：网页点击、托盘点击、实体「造物者模式」键；
  * 自适应自己换档一律不弹——干活干到一半屏幕上跳东西比没有提示更烦。
所以本模块只提供「给我文案」的函数，弹不弹由调用方（daemon）决定。

实体键三态（LED 亮度对应关系由用户 2026-09-30 现场确认，寄存器值由 tools/ec_watch.py 抓到）：
    全亮 = Turbo_Mode(0x10)        功耗墙 75W、风扇强冷     → 性能模式
    半亮 = Normal_Mode(0x00)       风扇与功耗墙都交给 EC     → 自适应模式
    不亮 = User_Fan_HiMode(0xA0)   功耗墙 10W、自定义曲线   → 省电模式

「不亮」这一态以前被写成「只是风扇自定义曲线、电源模式不变」，**是错的**：
2026-09-30 全表观察（GCUBridge 服务恢复后）抓到按一次键，`PL1_SETTING_VALUE`
同步 75 → 10、`MYFAN2_L1/L4_PWM`、`L2_PWM_DEFAULT_MYFAN3`、`DynamicBoost_MaxinumTGP`
整组跟着换，满载实测慢 23~26%（notes/hardware-channels.md 6.4）。所以它是一个完整的低功耗档，
文案必须这么说，否则用户看到面板还写着「自适应」会以为按键没生效。

⚠️ 但下面这些数字是**这一态的实际结果**，不是「按下按钮就能得到的承诺」：
2026-09-30 12:19 实测，面板写 `0x0751` 之后 PL1 死守原值 100 秒不动，
只有键盘那颗灯跟着变了——这个字节是服务 `SetUserProfile` 写出来的**输出**。
所以网页上的风扇按钮旁边必须跟一句「只改指示灯、改不动功耗墙」，
真要改墙走 OEM 硬件档按钮（GCUBridge 的 OPERATING_*）。
"""
# 硬件模式的映射表放在 app.act.channels.base（EC 通道也要用同一张表），
# 这里只包一层：文案层不许自己另存一份，否则两边迟早对不上。
from app.act.channels.base import hw_mode_of_fan_flag as hw_mode   # noqa: F401
from app.policy.scheduler import INTENT_NAMES, INTENT_TIER, TIER_LABELS

# 一张表定死「这个字节叫什么模式、机器实际发生了什么」。按键弹的那套是从它派生的
# （只加一句 LED 亮度），网页按钮弹的直接用它 —— 同一个字节不许有两套说法。
# 括号里的数字来自 notes/hardware-channels.md 6.2 的全表差分与 6.4 的满载实测，不是估的。
FAN_FLAG_TEXT = {'Normal_Mode': ('自适应模式', '风扇与功耗墙都交回 EC 自动'),
                 'Turbo_Mode': ('性能模式', '功耗墙 75W、风扇强冷，满载最快也最吵'),
                 'FanBoost_Mode': ('风扇加速', 'EC 短时把风量拉到最大，功耗墙不变')}

# LED 亮度 ↔ 取值：用户 2026-09-30 现场确认（GCUBridge 在跑时按键只循环全亮/不亮两态）。
KEY_LED = {'Turbo_Mode': '全亮', 'Normal_Mode': '半亮'}

FAN_KEY_TEXT = dict(
    (flag, (text[0], '实体键%s：%s' % (KEY_LED[flag], text[1]) if flag in KEY_LED else text[1]))
    for flag, text in FAN_FLAG_TEXT.items())

USER_FAN_TEXT = ('省电模式', '功耗墙降到 10W、风扇走自定义曲线，满载慢 23~26%')
USER_FAN_KEY_TEXT = ('省电模式', '实体键不亮：%s' % USER_FAN_TEXT[1])

# 实体键按出来的风扇字节 → 控制意图。User_Fan 全家族单独处理（见 key_intent）。
KEY_INTENT = {'Turbo_Mode': 'turbo', 'Normal_Mode': 'auto'}


def key_intent(flag):
    """实体键落到某一态时，控制意图该跟着变成什么。

    User_Fan 全家族（0x80 位：Mode/HiMode/Level1~5）都是低功耗档，
    所以映射到「锁定省电」——面板显示的意图必须和机器实际状态一致。
    认不出的取值返回 None：只弹提示，不动电源。
    """
    if flag and flag.startswith('User_Fan'):
        return 'office'
    return KEY_INTENT.get(flag)


def mode_words():
    """给网页用的「风扇字节 → 模式名」表。

    网页以前自己存了一份 JS 词表，结果和弹窗对不上号（用户报过）。现在词只在
    这里有一份，网页从 /api/state 的 meta 里拿。User_Fan 全家族共用一个名字，
    网页按前缀判断（app.js 的 fanModeWord）。
    """
    words = dict((k, v[0]) for k, v in FAN_FLAG_TEXT.items())
    words['User_Fan'] = USER_FAN_TEXT[0]
    return words


def key_text(flag):
    """实体键改完风扇字节后弹什么。认不出的取值照实说，不猜。"""
    if flag and flag.startswith('User_Fan'):
        return USER_FAN_KEY_TEXT
    got = FAN_KEY_TEXT.get(flag)
    if got:
        return got
    return '风扇模式变了', '当前取值 %s（不是按键三态之一）' % (flag or '未知')


def intent_text(intent):
    """人在网页/托盘上改了控制意图后弹什么。"""
    if intent == 'auto':
        return '自适应模式', '按负载和温度自动换档'
    tier = INTENT_TIER.get(intent)
    if tier is None:
        return '控制意图变了', '未知取值 %s' % intent
    return '%s模式' % TIER_LABELS[tier], '已锁定，不再自动切换'


def fan_text(flag):
    """人在网页/托盘上改了风扇策略后弹什么。"""
    got = FAN_FLAG_TEXT.get(flag)
    if got:
        return got
    if flag and flag.startswith('User_Fan'):
        return USER_FAN_TEXT
    return '风扇策略变了', '当前取值 %s' % (flag or '未知')


def profile_text(label, desc):
    return '调度性格：%s' % label, desc
