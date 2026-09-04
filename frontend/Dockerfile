# 构建上下文为仓库根目录：docker build -f backend/Dockerfile .

# ---- 构建阶段：Node 编译 ----
FROM node:22-alpine AS build

WORKDIR /build

# 使用国内 npm 镜像加速
RUN npm config set registry https://registry.npmmirror.com

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# ---- 运行阶段：Nginx 托管 + /api 反代 ----
FROM nginx:1.27-alpine

# 拷贝构建产物与 nginx 配置
COPY --from=build /build/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
