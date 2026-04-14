"""
Governance 模块 - 治理投票系统

提供去中心化治理功能：
- 创建治理提案
- 设置投票选项
- 指定参与人和投票权重
- 记录和统计投票结果

治理机制：
- 加权投票：不同参与者可以有不同的投票权重
- 多选项支持：支持二元投票或多元选择
- 灵活配置：可以设置投票截止时间、最低参与率等
"""

from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from decimal import Decimal

from .utils import to_decimal, D0
from .engine_node import EngineNode


class VoteStatus(Enum):
    """投票状态"""
    PENDING = "pending"      # 待开始
    ACTIVE = "active"        # 进行中
    CLOSED = "closed"        # 已结束
    EXECUTED = "executed"    # 已执行


@dataclass
class VoteRecord:
    """
    投票记录

    Attributes:
        voter: 投票者
        option: 选择的选项
        weight: 投票权重
        timestamp: 投票时间
    """
    voter: "Trader"
    option: str
    weight: Decimal
    timestamp: datetime = field(default_factory=datetime.now)


class GovernanceProposal(EngineNode):
    """
    治理提案

    代表一个治理投票实例，包含提案信息、投票选项、
    参与人及其权重、投票记录等。

    Attributes:
        id: 提案唯一标识
        title: 提案标题
        description: 提案描述
        creator: 提案创建者
        options: 投票选项列表
        participants: 参与人及其权重 {Trader: weight}
        votes: 投票记录列表
        status: 投票状态
        created_at: 创建时间
        end_time: 结束时间（可选）
        min_participation_rate: 最低参与率要求（0-1）

    Examples:
        >>> # 创建治理提案
        >>> proposal = GovernanceProposal(
        ...     id="PROP-001",
        ...     title="是否增发100万股",
        ...     description="公司计划增发100万股用于扩张",
        ...     creator=ceo,
        ...     options=["同意", "反对", "弃权"],
        ...     participants={shareholder1: Decimal('0.3'), shareholder2: Decimal('0.5'), shareholder3: Decimal('0.2')}
        ... )
        >>>
        >>> # 投票
        >>> proposal.cast_vote(shareholder1, "同意")
        >>> proposal.cast_vote(shareholder2, "同意")
        >>>
        >>> # 统计结果
        >>> result = proposal.tally_votes()
        >>> print(f"获胜选项: {result['winner']}")
    """

    _id_counter = 0

    def __init__(
        self,
        title: str,
        description: str,
        creator: "Trader",
        options: List[str],
        participants: Dict["Trader", Decimal],
        end_time: Optional[datetime] = None,
        min_participation_rate: Decimal = D0,
        proposal_id: Optional[str] = None
    ):
        """
        创建治理提案

        Args:
            title: 提案标题
            description: 提案描述
            creator: 提案创建者
            options: 投票选项列表
            participants: 参与人及其权重 {Trader: weight}
            end_time: 投票结束时间（可选）
            min_participation_rate: 最低参与率要求（0-1）
            proposal_id: 自定义提案ID（可选）
        """
        super().__init__(f"{title[:20]}...")
        # 生成唯一ID
        if proposal_id:
            self.id = proposal_id
        else:
            GovernanceProposal._id_counter += 1
            self.id = f"PROP-{GovernanceProposal._id_counter:04d}"

        self.title = title
        self.description = description
        self.creator = creator
        self.options = options
        # 转换权重为Decimal
        self.participants = {k: to_decimal(v) for k, v in participants.items()}
        self.votes: List[VoteRecord] = []
        self.status = VoteStatus.ACTIVE
        self.created_at = datetime.now()
        self.end_time = end_time
        self.min_participation_rate = to_decimal(min_participation_rate)

        # 验证权重总和是否为1
        total_weight = sum(self.participants.values())
        if abs(total_weight - Decimal('1.0')) > Decimal('0.0001'):
            raise ValueError(f"参与人权重总和必须等于1，当前为 {total_weight}")

        # 验证选项非空
        if not options:
            raise ValueError("投票选项不能为空")

    def cast_vote(self, voter: "Trader", option: str) -> bool:
        """
        投票

        投票后会调用 voter 的 on_vote_cast 方法，
        如果达到最低参与率，还会调用 creator 的 on_proposal_reached_quorum 方法。

        Args:
            voter: 投票者
            option: 选择的选项

        Returns:
            是否投票成功

        Raises:
            ValueError: 如果投票者不是参与人、选项无效或投票已结束
        """
        # 检查投票状态
        if self.status != VoteStatus.ACTIVE:
            raise ValueError(f"投票状态为 {self.status.value}，无法投票")

        # 检查是否已过截止时间
        if self.end_time and datetime.now() > self.end_time:
            raise ValueError("投票已截止")

        # 检查投票者是否为参与人
        if voter not in self.participants:
            raise ValueError(f"{voter.name} 不是本次投票的参与人")

        # 检查选项是否有效
        if option not in self.options:
            raise ValueError(f"无效选项: {option}，有效选项为 {self.options}")

        # 检查是否已经投过票
        for vote in self.votes:
            if vote.voter is voter:
                raise ValueError(f"{voter.name} 已经投过票")

        # 记录投票
        weight = self.participants[voter]
        vote_record = VoteRecord(
            voter=voter,
            option=option,
            weight=weight
        )
        self.votes.append(vote_record)

        # 调用投票者的回调方法
        try:
            voter.on_vote_cast(self, option, weight)
        except Exception as e:
            # 回调失败不应影响投票流程，但应该记录
            print(f"警告: 投票者 {voter.name} 的 on_vote_cast 回调失败: {e}")

        # 检查是否达到最低参与率
        result = self.tally_votes()
        if result['is_valid']:
            # 调用提案创建者的回调方法
            try:
                self.creator.on_proposal_reached_quorum(self, result)
            except Exception as e:
                # 回调失败不应影响投票流程，但应该记录
                print(f"警告: 提案创建者 {self.creator.name} 的 on_proposal_reached_quorum 回调失败: {e}")
            # 达到最低参与率后自动关闭提案
            self.close_voting()

        return True

    def change_vote(self, voter: "Trader", new_option: str) -> bool:
        """
        修改投票（如果允许）

        Args:
            voter: 投票者
            new_option: 新选择的选项

        Returns:
            是否修改成功
        """
        # 检查投票状态
        if self.status != VoteStatus.ACTIVE:
            raise ValueError(f"投票状态为 {self.status.value}，无法修改投票")

        # 检查是否已过截止时间
        if self.end_time and datetime.now() > self.end_time:
            raise ValueError("投票已截止")

        # 查找并修改投票
        for vote in self.votes:
            if vote.voter is voter:
                if new_option not in self.options:
                    raise ValueError(f"无效选项: {new_option}")
                vote.option = new_option
                vote.timestamp = datetime.now()
                return True

        raise ValueError(f"{voter.name} 尚未投票，无法修改")

    def tally_votes(self) -> Dict[str, Any]:
        """
        统计投票结果

        Returns:
            {
                "proposal_id": 提案ID,
                "title": 提案标题,
                "status": 投票状态,
                "total_votes": 总投票数,
                "total_weight": 总投票权重,
                "participation_rate": 参与率,
                "results": {选项: {"count": 票数, "weight": 权重}},
                "winner": 获胜选项（权重最高）,
                "winner_weight": 获胜选项权重,
                "winner_percentage": 获胜选项占比,
                "is_valid": 是否有效（达到最低参与率）
            }
        """
        # 统计各选项得票
        results: Dict[str, Dict[str, Any]] = {}
        for option in self.options:
            results[option] = {"count": 0, "weight": D0}

        total_weight_cast = D0
        for vote in self.votes:
            results[vote.option]["count"] += 1
            results[vote.option]["weight"] += vote.weight
            total_weight_cast += vote.weight

        # 计算参与率
        total_participant_weight = sum(self.participants.values())
        participation_rate = total_weight_cast / total_participant_weight if total_participant_weight > D0 else D0

        # 判断是否达到最低参与率
        is_valid = participation_rate >= self.min_participation_rate

        # 找出获胜选项（按权重）
        winner = None
        winner_weight = D0
        for option, data in results.items():
            if data["weight"] > winner_weight:
                winner = option
                winner_weight = data["weight"]

        winner_percentage = (winner_weight / total_weight_cast * Decimal('100')) if total_weight_cast > D0 else D0

        return {
            "proposal_id": self.id,
            "title": self.title,
            "status": self.status.value,
            "total_votes": len(self.votes),
            "total_weight": total_weight_cast,
            "participation_rate": participation_rate,
            "results": results,
            "winner": winner,
            "winner_weight": winner_weight,
            "winner_percentage": winner_percentage,
            "is_valid": is_valid
        }

    def close_voting(self) -> None:
        """结束投票"""
        if self.status == VoteStatus.ACTIVE:
            self.status = VoteStatus.CLOSED

    def execute(self) -> None:
        """标记为已执行"""
        if self.status == VoteStatus.CLOSED:
            self.status = VoteStatus.EXECUTED

    def get_voter_choice(self, voter: "Trader") -> Optional[str]:
        """
        获取指定投票者的选择

        Args:
            voter: 投票者

        Returns:
            选择的选项，如果未投票则返回 None
        """
        for vote in self.votes:
            if vote.voter is voter:
                return vote.option
        return None

    def has_voted(self, voter: "Trader") -> bool:
        """
        检查指定投票者是否已经投票

        Args:
            voter: 投票者

        Returns:
            是否已投票
        """
        return any(vote.voter is voter for vote in self.votes)

    def get_pending_voters(self) -> List["Trader"]:
        """
        获取尚未投票的参与人列表

        Returns:
            未投票的参与人列表
        """
        voted = {vote.voter for vote in self.votes}
        return [p for p in self.participants.keys() if p not in voted]

    def __repr__(self) -> str:
        return f"GovernanceProposal({self.id}: {self.title})"


class GovernanceSystem(EngineNode):
    """
    治理系统

    管理多个治理提案，提供统一的创建和查询接口。

    Examples:
        >>> governance = GovernanceSystem()
        >>>
        >>> # 创建提案
        >>> proposal = governance.create_proposal(
        ...     title="是否通过Q2预算",
        ...     description="Q2预算总额为100万USDT",
        ...     creator=ceo,
        ...     options=["同意", "反对"],
        ...     participants=shareholders
        ... )
        >>>
        >>> # 获取所有活跃提案
        >>> active = governance.get_active_proposals()
    """

    def __init__(self):
        """初始化治理系统"""
        super().__init__("GovernanceSystem")
        self.proposals: Dict[str, GovernanceProposal] = {}

    def create_proposal(
        self,
        title: str,
        description: str,
        creator: "Trader",
        options: List[str],
        participants: Dict["Trader", Decimal],
        end_time: Optional[datetime] = None,
        min_participation_rate: Decimal = D0,
        proposal_id: Optional[str] = None
    ) -> GovernanceProposal:
        """
        创建新的治理提案

        Args:
            title: 提案标题
            description: 提案描述
            creator: 提案创建者
            options: 投票选项列表
            participants: 参与人及其权重
            end_time: 投票结束时间（可选）
            min_participation_rate: 最低参与率要求
            proposal_id: 自定义提案ID（可选）

        Returns:
            创建的治理提案
        """
        proposal = GovernanceProposal(
            title=title,
            description=description,
            creator=creator,
            options=options,
            participants=participants,
            end_time=end_time,
            min_participation_rate=min_participation_rate,
            proposal_id=proposal_id
        )

        self.proposals[proposal.id] = proposal
        return proposal

    def get_proposal(self, proposal_id: str) -> Optional[GovernanceProposal]:
        """
        获取指定提案

        Args:
            proposal_id: 提案ID

        Returns:
            治理提案或 None
        """
        return self.proposals.get(proposal_id)

    def get_all_proposals(self) -> List[GovernanceProposal]:
        """
        获取所有提案

        Returns:
            所有提案列表
        """
        return list(self.proposals.values())

    def get_active_proposals(self) -> List[GovernanceProposal]:
        """
        获取所有活跃（进行中）的提案

        Returns:
            活跃提案列表
        """
        return [p for p in self.proposals.values() if p.status == VoteStatus.ACTIVE]

    def get_proposals_by_status(self, status: VoteStatus) -> List[GovernanceProposal]:
        """
        获取指定状态的提案

        Args:
            status: 投票状态

        Returns:
            指定状态的提案列表
        """
        return [p for p in self.proposals.values() if p.status == status]

    def get_proposals_by_participant(self, participant: "Trader") -> List[GovernanceProposal]:
        """
        获取指定参与人可以投票的所有提案

        Args:
            participant: 参与人

        Returns:
            该参与人可以投票的提案列表
        """
        return [
            p for p in self.proposals.values()
            if participant in p.participants
        ]

    def close_expired_proposals(self) -> List[GovernanceProposal]:
        """
        关闭所有已过期的提案

        Returns:
            被关闭的提案列表
        """
        closed = []
        now = datetime.now()
        for proposal in self.proposals.values():
            if (proposal.status == VoteStatus.ACTIVE and
                proposal.end_time and
                now > proposal.end_time):
                proposal.close_voting()
                closed.append(proposal)
        return closed
