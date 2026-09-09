"""python -m app：仅监听当前电脑，禁止误当公网服务器。"""
import argparse
import uvicorn

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--port',type=int,default=8000)
    args=p.parse_args()
    uvicorn.run('app.main:create_app',factory=True,host='127.0.0.1',port=args.port,workers=1)

if __name__=='__main__':main()
