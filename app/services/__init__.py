"""业务编排层。

只依赖 domain.ports 的抽象接口，由 api/deps.py 注入具体实现。
import-linter 禁止本层 import repositories / integrations 具体模块。
"""
