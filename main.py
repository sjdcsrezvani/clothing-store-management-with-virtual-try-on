import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from database import engine, Base
from routers import customers, api, admin, products, sales, analytics, campaigns
from services.scheduler import scheduler_task


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    scheduler = asyncio.create_task(scheduler_task())
    yield
    scheduler.cancel()
    try:
        await scheduler
    except asyncio.CancelledError:
        pass


app = FastAPI(title="رای کیدز — سیستم فروش", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(customers.router)
app.include_router(api.router)
app.include_router(admin.router)
app.include_router(products.router)
app.include_router(sales.router)
app.include_router(analytics.router)
app.include_router(campaigns.router)
