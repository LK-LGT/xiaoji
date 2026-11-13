"""【板块4】考勤调整规则 - 灵活配置系统

设计目标：
  1. 不改代码，只改配置
  2. 支持任意复杂的规则
  3. 易于与人事部门沟通
  4. 可立即生效，无需重启
"""

from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Dict, Optional


class AttendanceRuleType(str, Enum):
    """考勤调整规则类型"""
    
    SIMPLE = "simple"  # 简单模式：每天固定扣分
    TIERED = "tiered"  # 分层模式：不同天数范围不同扣分
    PROGRESSIVE = "progressive"  # 递进模式：按周期递增扣分
    CUSTOM = "custom"  # 自定义模式：完全自定义规则


# ─────────────────────────────────────────────────────────────
# 简单模式：每天固定扣分
# ─────────────────────────────────────────────────────────────


class SimpleAttendanceRule(BaseModel):
    """简单模式 - 每天固定扣分"""
    
    rule_type: str = Field("simple", description="规则类型")
    deduction_per_day: float = Field(2.0, description="每天扣分（正数）")
    max_deduction: Optional[float] = Field(None, description="最多扣分上限（无上限则为 None）")
    
    class Config:
        schema_extra = {
            "example": {
                "rule_type": "simple",
                "deduction_per_day": 2.0,
                "max_deduction": 50.0,
                "description": "每天 2 分，最多扣 50 分"
            }
        }
    
    def calculate(self, absent_days: int) -> float:
        """计算扣分"""
        if absent_days <= 0:
            return 0.0
        
        deduction = absent_days * self.deduction_per_day
        
        if self.max_deduction:
            deduction = min(deduction, self.max_deduction)
        
        return -deduction


# ─────────────────────────────────────────────────────────────
# 分层模式：不同天数范围不同扣分
# ─────────────────────────────────────────────────────────────


class AttendanceTier(BaseModel):
    """考勤分层定义"""
    
    tier_name: str = Field(..., description="分层名称，如 '第1周'")
    days_from: int = Field(..., ge=1, description="天数范围起始（含）")
    days_to: int = Field(..., description="天数范围结束（含）")
    deduction_per_day: float = Field(..., description="该分层内每天扣分（正数）")


class TieredAttendanceRule(BaseModel):
    """分层模式 - 不同天数范围不同扣分"""
    
    rule_type: str = Field("tiered", description="规则类型")
    tiers: List[AttendanceTier] = Field(..., description="分层规则列表")
    max_deduction: Optional[float] = Field(None, description="最多扣分上限")
    
    class Config:
        schema_extra = {
            "example": {
                "rule_type": "tiered",
                "tiers": [
                    {
                        "tier_name": "第 1 周",
                        "days_from": 1,
                        "days_to": 7,
                        "deduction_per_day": 2.0
                    },
                    {
                        "tier_name": "第 2 周",
                        "days_from": 8,
                        "days_to": 14,
                        "deduction_per_day": 3.0
                    },
                    {
                        "tier_name": "第 3+ 周",
                        "days_from": 15,
                        "days_to": 999,
                        "deduction_per_day": 4.0
                    }
                ],
                "max_deduction": None,
                "description": "7 天为周期，递进式扣分"
            }
        }
    
    def calculate(self, absent_days: int) -> float:
        """计算扣分"""
        if absent_days <= 0:
            return 0.0
        
        total_deduction = 0.0
        remaining_days = absent_days
        
        # 按分层顺序计算
        for tier in self.tiers:
            if remaining_days <= 0:
                break
            
            # 计算该分层涉及的天数
            tier_range = tier.days_to - tier.days_from + 1
            days_in_tier = min(remaining_days, tier_range)
            
            # 该分层的扣分
            tier_deduction = days_in_tier * tier.deduction_per_day
            total_deduction += tier_deduction
            
            # 剩余天数
            remaining_days -= days_in_tier
        
        # 应用上限
        if self.max_deduction:
            total_deduction = min(total_deduction, self.max_deduction)
        
        return -total_deduction


# ─────────────────────────────────────────────────────────────
# 递进模式（特殊的分层模式的简化版）
# ─────────────────────────────────────────────────────────────


class ProgressiveAttendanceRule(BaseModel):
    """递进模式 - 按周期递增扣分（7 天为周期）"""
    
    rule_type: str = Field("progressive", description="规则类型")
    week_length: int = Field(7, description="周期长度（天）")
    tier1_deduction: float = Field(2.0, description="第 1 周期每天扣分")
    tier2_deduction: float = Field(3.0, description="第 2 周期每天扣分")
    tier3_plus_deduction: float = Field(4.0, description="第 3+ 周期每天扣分")
    max_deduction: Optional[float] = Field(None, description="最多扣分上限")
    
    class Config:
        schema_extra = {
            "example": {
                "rule_type": "progressive",
                "week_length": 7,
                "tier1_deduction": 2.0,
                "tier2_deduction": 3.0,
                "tier3_plus_deduction": 4.0,
                "max_deduction": None,
                "description": "7 天为周期，递进式扣分"
            }
        }
    
    def calculate(self, absent_days: int) -> float:
        """计算扣分"""
        if absent_days <= 0:
            return 0.0
        
        week_len = self.week_length
        total_deduction = 0.0
        
        # 第 1 周
        week1_days = min(absent_days, week_len)
        total_deduction += week1_days * self.tier1_deduction
        
        if absent_days <= week_len:
            return -min(total_deduction, self.max_deduction or total_deduction)
        
        # 第 2 周
        week2_days = min(absent_days - week_len, week_len)
        total_deduction += week2_days * self.tier2_deduction
        
        if absent_days <= 2 * week_len:
            return -min(total_deduction, self.max_deduction or total_deduction)
        
        # 第 3+ 周
        week3_plus_days = absent_days - 2 * week_len
        total_deduction += week3_plus_days * self.tier3_plus_deduction
        
        deduction = min(total_deduction, self.max_deduction or total_deduction)
        return -deduction


# ─────────────────────────────────────────────────────────────
# 自定义模式：完全灵活
# ─────────────────────────────────────────────────────────────


class CustomAttendanceRule(BaseModel):
    """自定义模式 - 任意复杂规则"""
    
    rule_type: str = Field("custom", description="规则类型")
    description: str = Field(..., description="规则描述")
    rules: List[Dict] = Field(..., description="自定义规则列表（待实现）")
    
    class Config:
        schema_extra = {
            "example": {
                "rule_type": "custom",
                "description": "按国家劳动法规定的规则",
                "rules": [
                    {"condition": "days <= 3", "deduction": 1.0},
                    {"condition": "3 < days <= 7", "deduction": 2.0},
                    {"condition": "days > 7", "deduction": 3.0}
                ]
            }
        }


# ─────────────────────────────────────────────────────────────
# 统一的考勤规则配置
# ─────────────────────────────────────────────────────────────


class AttendanceRuleConfig(BaseModel):
    """考勤规则配置 - 支持多种规则类型"""
    
    active_rule_type: AttendanceRuleType = Field(
        ..., 
        description="当前激活的规则类型"
    )
    
    # 支持的所有规则类型
    simple_rule: Optional[SimpleAttendanceRule] = None
    tiered_rule: Optional[TieredAttendanceRule] = None
    progressive_rule: Optional[ProgressiveAttendanceRule] = None
    custom_rule: Optional[CustomAttendanceRule] = None
    
    class Config:
        schema_extra = {
            "examples": {
                "simple": {
                    "active_rule_type": "simple",
                    "simple_rule": {
                        "rule_type": "simple",
                        "deduction_per_day": 2.0,
                        "max_deduction": 50.0
                    }
                },
                "tiered": {
                    "active_rule_type": "tiered",
                    "tiered_rule": {
                        "rule_type": "tiered",
                        "tiers": [
                            {"tier_name": "第 1 周", "days_from": 1, "days_to": 7, "deduction_per_day": 2.0},
                            {"tier_name": "第 2 周", "days_from": 8, "days_to": 14, "deduction_per_day": 3.0},
                            {"tier_name": "第 3+ 周", "days_from": 15, "days_to": 999, "deduction_per_day": 4.0}
                        ],
                        "max_deduction": None
                    }
                },
                "progressive": {
                    "active_rule_type": "progressive",
                    "progressive_rule": {
                        "rule_type": "progressive",
                        "week_length": 7,
                        "tier1_deduction": 2.0,
                        "tier2_deduction": 3.0,
                        "tier3_plus_deduction": 4.0,
                        "max_deduction": None
                    }
                }
            }
        }
    
    def calculate_deduction(self, absent_days: int) -> float:
        """根据当前激活的规则计算扣分"""
        
        if self.active_rule_type == AttendanceRuleType.SIMPLE:
            if not self.simple_rule:
                return 0.0
            return self.simple_rule.calculate(absent_days)
        
        elif self.active_rule_type == AttendanceRuleType.TIERED:
            if not self.tiered_rule:
                return 0.0
            return self.tiered_rule.calculate(absent_days)
        
        elif self.active_rule_type == AttendanceRuleType.PROGRESSIVE:
            if not self.progressive_rule:
                return 0.0
            return self.progressive_rule.calculate(absent_days)
        
        else:
            # 自定义模式暂不支持
            return 0.0


# ─────────────────────────────────────────────────────────────
# 预设配置模板
# ─────────────────────────────────────────────────────────────


ATTENDANCE_RULE_PRESETS = {
    "original": {
        "name": "原规则（待替换）",
        "description": "缺勤 1-3 天 -2 分、3-5 天 -5 分、5+ 天 -10 分",
        "config": AttendanceRuleConfig(
            active_rule_type="tiered",
            tiered_rule=TieredAttendanceRule(
                rule_type="tiered",
                tiers=[
                    AttendanceTier(tier_name="1-3 天", days_from=1, days_to=3, deduction_per_day=2.0),
                    AttendanceTier(tier_name="3-5 天", days_from=4, days_to=5, deduction_per_day=5.0),
                    AttendanceTier(tier_name="5+ 天", days_from=6, days_to=999, deduction_per_day=10.0),
                ],
                max_deduction=None
            )
        )
    },
    
    "simple_2_per_day": {
        "name": "简单模式：每天 2 分",
        "description": "每天固定扣 2 分，最多扣 50 分",
        "config": AttendanceRuleConfig(
            active_rule_type="simple",
            simple_rule=SimpleAttendanceRule(
                rule_type="simple",
                deduction_per_day=2.0,
                max_deduction=50.0
            )
        )
    },
    
    "tiered_weekly": {
        "name": "分层模式：7 天周期",
        "description": "第 1 周 -2 分/天、第 2 周 -3 分/天、第 3+ 周 -4 分/天",
        "config": AttendanceRuleConfig(
            active_rule_type="tiered",
            tiered_rule=TieredAttendanceRule(
                rule_type="tiered",
                tiers=[
                    AttendanceTier(tier_name="第 1 周", days_from=1, days_to=7, deduction_per_day=2.0),
                    AttendanceTier(tier_name="第 2 周", days_from=8, days_to=14, deduction_per_day=3.0),
                    AttendanceTier(tier_name="第 3+ 周", days_from=15, days_to=999, deduction_per_day=4.0),
                ],
                max_deduction=None
            )
        )
    },
    
    "progressive_weekly": {
        "name": "递进模式：7 天周期",
        "description": "7 天为周期，按周期递进扣分",
        "config": AttendanceRuleConfig(
            active_rule_type="progressive",
            progressive_rule=ProgressiveAttendanceRule(
                rule_type="progressive",
                week_length=7,
                tier1_deduction=2.0,
                tier2_deduction=3.0,
                tier3_plus_deduction=4.0,
                max_deduction=None
            )
        )
    },
}


# ─────────────────────────────────────────────────────────────
# 使用示例
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    
    print("=" * 60)
    print("【板块4】考勤调整规则 - 灵活配置系统")
    print("=" * 60)
    
    # 遍历所有预设
    for preset_key, preset in ATTENDANCE_RULE_PRESETS.items():
        print(f"\n📋 {preset['name']}")
        print(f"   {preset['description']}")
        
        config = preset['config']
        
        # 测试几个典型场景
        test_days = [0, 1, 5, 7, 10, 14, 15, 20]
        
        print(f"\n   缺勤天数 → 扣分：")
        for days in test_days:
            deduction = config.calculate_deduction(days)
            print(f"   {days:2d} 天 → {deduction:6.0f} 分")
    
    print("\n" + "=" * 60)
    print("💡 使用指南：")
    print("=" * 60)
    print("""
1. 与人事部门确认需要的规则类型：
   ✓ simple: 每天固定扣分
   ✓ tiered: 不同天数范围不同扣分
   ✓ progressive: 按周期递进扣分
   ✓ custom: 完全自定义

2. 获得具体的参数后，更新 ATTENDANCE_RULE_PRESETS

3. 在规则引擎中使用：
   config = ATTENDANCE_RULE_PRESETS["你选择的规则"].config
   deduction = config.calculate_deduction(absent_days)

4. 零代码改动，只需修改配置！
    """)
