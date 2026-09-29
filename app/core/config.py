from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./db.sqlite3"
    ENV: str = "development"
    SECRET_KEY: str = "development-secret-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120

    COOKIE_NAME: str = "access_token"
    COOKIE_MAX_AGE: int = 60 * 60 * 24 * 1  # 1 days in seconds
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "strict"
    
    COMPANY_NAME: str = "X"
    COMPANY_COLOR1: str = "#21358b"
    COMPANY_COLOR2: str = "#d7140e"

    # Data Panels (embedded Dash dashboards)
    APP_TITLE: str = "Veri Panoları"          # dashboards' landing header / tab title
    DATA1_PATH: str = "dummy_data1.xlsx"       # relative paths resolve under app/static/data
    DATA2_PATH: str = "dummy_data2.xlsx"
    DATA3_PATH: str = "dummy_data3.xlsx"
    DATA4_PATH: str = "dummy_data4.xlsx"
    COMPANY_VIDEOS: str = (
        "/static/images/video1.mp4, "
        "/static/images/video3.mp4, "
        "/static/images/video4.mp4, "
        "/static/images/video5.mp4"
    )
    
    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()