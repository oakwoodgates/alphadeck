# Alpha Deck frontend — build the Vite SPA, then serve it (and proxy the API) with nginx.
FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build   # tsc -b (strict typecheck) + vite build -> /app/dist (uses the committed types.gen.ts)

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
# nginx:alpine's default CMD runs `nginx -g 'daemon off;'`.
