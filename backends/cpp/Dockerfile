# Drogon built from source in C++20 (coroutines), PostgreSQL only.
# LIBPQ_BATCH_MODE=OFF: one query in flight per connection like every other driver here
# (pipeline mode would be an unfair advantage, and it drops SQLSTATE from errors).
FROM ubuntu:24.04 AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
      g++ cmake make git ca-certificates libjsoncpp-dev uuid-dev zlib1g-dev libssl-dev libpq-dev \
 && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch v1.9.10 --recurse-submodules --shallow-submodules https://github.com/drogonframework/drogon /drogon \
 && cmake -S /drogon -B /drogon/build -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_STANDARD=20 \
      -DBUILD_EXAMPLES=OFF -DBUILD_CTL=OFF -DBUILD_MYSQL=OFF -DBUILD_SQLITE=OFF -DBUILD_REDIS=OFF -DBUILD_ORM=ON -DLIBPQ_BATCH_MODE=OFF \
 && cmake --build /drogon/build -j && cmake --install /drogon/build
WORKDIR /src
COPY CMakeLists.txt main.cc ./
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j

FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends libjsoncpp25 libpq5 libssl3t64 zlib1g libuuid1 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/build/api /api
EXPOSE 8080
ENTRYPOINT ["/api"]
