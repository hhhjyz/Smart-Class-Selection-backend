"""HTTP 请求 / 响应 DTO（Pydantic）。

接入层入参与出参都走本包，禁止 handler 直接收发裸 dict。
所有响应统一包进 ``Envelope`` 响应壳。
"""
