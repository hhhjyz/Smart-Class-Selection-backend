"""领域层：业务实体与抽象端口（ports）。

实体是层间传递的契约对象（Pydantic, extra=forbid），禁止裸 dict 穿梭。
ports 定义高层依赖的抽象接口，由 repositories / integrations 实现，
体现依赖倒置：services 只 import 本包，不 import 具体实现。
"""
