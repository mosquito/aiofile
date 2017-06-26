linux_wheel:
	docker run -it --rm \
		-v `pwd`:/app/src:ro \
		-v `pwd`/dist:/app/dst \
		--entrypoint /bin/bash \
		quay.io/pypa/manylinux1_x86_64 \
		/app/src/scripts/make-wheels.sh
