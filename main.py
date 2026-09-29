import uvicorn

from app import create_app
from app.config import settings
from app.db.init_db import init_db

app = create_app()

# 建表 + 播种角色（幂等，重复启动不会出问题）。
# 放在 main.py 而不是 create_app() 里：测试时只调 create_app()，完全不碰真实数据库。
init_db()

if __name__ == "__main__":
    # host / port / reload 都从 .env 读：
    #   APP_HOST=0.0.0.0 可让同局域网的手机、另一台电脑访问
    uvicorn.run(
        "main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.APP_RELOAD,
    )
