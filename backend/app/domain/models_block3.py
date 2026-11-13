"""【板块3】数据库 ORM 模型

新增表：
  1. SelfReview - 员工自评表
  2. ManagerReview - 主管评分表
  3. MetricValue - 指标值表（扩展）
"""

from sqlalchemy import Column, String, Integer, Float, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from uuid import uuid4

from backend.app.core.db import Base


class SelfReview(Base):
    """【板块3.1】员工自评表"""
    
    __tablename__ = "self_review"
    
    # 主键
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    
    # 外键
    record_id = Column(String, ForeignKey("assessment_record.id"), nullable=False)
    employee_id = Column(String, ForeignKey("employee.id"), nullable=False)
    
    # 自评数据
    self_score = Column(Integer, nullable=False)  # 1-5 分
    self_comment = Column(Text, nullable=False)  # 自评评论
    highlights = Column(Text, nullable=True)  # 亮点
    improvement_areas = Column(Text, nullable=True)  # 改进方向
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    submitted_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
    
    # 关系
    assessment_record = relationship("AssessmentRecord", backref="self_reviews")
    employee = relationship("Employee", foreign_keys=[employee_id])
    
    def __repr__(self):
        return f"<SelfReview(id={self.id}, record_id={self.record_id}, score={self.self_score})>"


class ManagerReview(Base):
    """【板块3.2】主管评分表"""
    
    __tablename__ = "manager_review"
    
    # 主键
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    
    # 外键
    record_id = Column(String, ForeignKey("assessment_record.id"), nullable=False)
    manager_id = Column(String, ForeignKey("employee.id"), nullable=False)
    
    # 评分数据
    manager_score = Column(Integer, nullable=False)  # 1-5 分
    manager_comment = Column(Text, nullable=False)  # 主管评论
    rating_reason = Column(Text, nullable=True)  # 评分理由
    recommended_action = Column(Text, nullable=True)  # 建议行动
    
    # 初步得分（规则引擎前）
    preliminary_score = Column(Float, nullable=True)
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    submitted_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
    
    # 关系
    assessment_record = relationship("AssessmentRecord", backref="manager_reviews")
    manager = relationship("Employee", foreign_keys=[manager_id])
    
    def __repr__(self):
        return f"<ManagerReview(id={self.id}, record_id={self.record_id}, score={self.manager_score})>"
