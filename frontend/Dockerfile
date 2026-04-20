# ============================================================
# Stage 1: Builder — CRA 프로덕션 빌드
# ============================================================
FROM node:18-alpine AS builder

WORKDIR /app

# 의존성 레이어 캐싱: package*.json 먼저 복사
COPY package*.json ./
RUN npm install

# 나머지 소스 복사
COPY . .

# 빌드 인자: 기본값은 빈 문자열 (런타임에 window.location 기반 동적 판단)
ARG REACT_APP_API_URL=""
ENV REACT_APP_API_URL=$REACT_APP_API_URL

# 프로덕션 빌드: build/ 에 해시된 static 자산 생성
RUN npm run build


# ============================================================
# Stage 2: Serve — nginx로 정적 서빙
# ============================================================
FROM nginx:alpine AS serve

# builder에서 빌드 결과물 복사
COPY --from=builder /app/build /usr/share/nginx/html

# nginx 설정 (캐시 정책 + SPA 라우팅 + gzip)
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
