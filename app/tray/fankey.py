# -*- coding: utf-8 -*-
"""屏幕提示（OSD）的文案：全项目只有一套模式词。

词表在 app.policy.scheduler：省电/均衡/流畅/性能 四个模式，意图只是
「自适应 / 锁定某模式」。这里不再出现「办公」「狂暴」「造物者 开/关」
这类第二套说法——机主反馈过网页一套词、弹窗一套词，对不上号。

什么时候该弹、什么时候闭嘴（机主定的规矩）：
  * 人动手才弹：网页点击、托盘点击、实体「造物者模式」键；
  * 自适应自己换档一律不弹——干活干到一半屏幕上跳东西比没有提示更烦。
所以本模块只提供「给我文案」的函数，弹不弹由调用方（daemon）决定。

实体键三态是实测出来的（tools/ec_watch.py，README 6.2）：
    Normal_Mode(0x00) → User_Fan_Mode(0x80) → Turbo_Mode(0x10) → 回到 0x00
LED 亮度和哪一态对应至今没有证据，文案里不写「全亮/半亮」。
"""
from app.policy.scheduler import INTENT_NAMES, INTENT_TIER, TIER_LABELS

# 括号里的数字来自 README 6.4 的满载实测，不是估的。
FAN_KEY_TEXT = {
    'Turbo_Mode': ('性能模式', '实体键已开：风扇强冷，电源锁定性能'),
    'Normal_Mode': ('自适应模式', '实体键已关：风扇与电源都交回自动'),
    'FanBoost_Mode': ('风扇加速', 'EC 短时把风量拉到最大，电源模式不变'),
}

USER_FAN_TEXT = ('风扇：自定义曲线', '电源模式不变，面板不再自动改风扇')

FAN_FLAG_TEXT = {'Normal_Mode': ('风扇：自动', '转速交给 EC 按温度调'),
                 'Turbo_Mode': ('风扇：强冷', '满载快 5~9%，风扇会很吵'),
                 'FanBoost_Mode': ('风扇：加速', 'EC 短时把风量拉到最大')}

# 实体键按出来的风扇字节 → 控制意图。查不到就是「这一态不动电源」：
# 自定义曲线只管风扇、认不出的取值不猜。放在这里而不是 daemon 里，
# 是为了脱离硬件也能单测这条对应关系。
KEY_INTENT = {'Turbo_Mode': 'turbo', 'Normal_Mode': 'auto'}


def key_text(flag):
    """实体键改完风扇字节后弹什么。认不出的取值照实说，不猜。"""
    if flag and flag.startswith('User_Fan'):
        return USER_FAN_TEXT
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
