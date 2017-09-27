build: sdist mac_wheel linux_wheel

sdist:
	python3 setup.py sdist

mac_wheel:
	python3.4 setup.py bdist_wheel
	python3.5 setup.py bdist_wheel
	python3.6 setup.py bdist_wheel

linux_wheel:
	docker run -it --rm \
		-v `pwd`:/app/src:ro \
		-v `pwd`/dist:/app/dst \
		--entrypoint /bin/bash \
		quay.io/pypa/manylinux1_x86_64 \
		/app/src/scripts/make-wheels.sh
