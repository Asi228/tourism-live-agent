import uvicorn

from app import create_app
from app.config import settings

app = create_app()

if __name__ == "__main__":
    # host / port / reload 都从 .env 读：
    #   APP_HOST=0.0.0.0 可让同局域网的手机、另一台电脑访问
    uvicorn.run(
        "main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.APP_RELOAD,
    )
