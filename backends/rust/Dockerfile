FROM rust:1.88-slim AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo 'fn main() {}' > src/main.rs && cargo build --release && rm -rf src target/release/deps/api*
COPY src src
RUN cargo build --release

FROM debian:bookworm-slim
COPY --from=build /src/target/release/api /api
EXPOSE 8080
ENTRYPOINT ["/api"]
