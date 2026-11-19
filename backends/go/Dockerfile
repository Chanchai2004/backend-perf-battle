FROM golang:1.25-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /api .

FROM gcr.io/distroless/static-debian12
COPY --from=build /api /api
# Pinned to the 2-CPU limit (Go 1.25 also reads the cgroup quota; explicit is clearer).
ENV GOMAXPROCS=2 GIN_MODE=release
EXPOSE 8080
ENTRYPOINT ["/api"]
