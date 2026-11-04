# Поднять удалённый HTTPS-прокси

Пример для администратора выделенного Ubuntu 24.04 с постоянным публичным IPv4. Клиенты подключаются к **самому серверу** по TLS на порту 443. SSH нужен администратору для развёртывания, но не пользователям. Для уже работающего прокси достаточно [клиентской настройки](proxy-client.md).

Подставьте свой постоянный IPv4 вместо `YOUR_PUBLIC_IPV4`. Если 443 уже занят, выделите другой порт прокси и измените `https_port`, оба файрвола и клиентские URL. Порт 80 нужен для ACME HTTP-01. Инструкция рассчитана на сервер без другой установки Squid или Certbot. Сохраните действующие настройки перед изменениями.

## Пакеты и сертификат

Разрешите TCP 80 и 443 на IPv4 в сетевом файрволе (в AWS это security group) и в уже активном UFW. Сохраните свой доступ SSH. Не открывайте порт 3128, внутренние сервисы или метаданные облака. Выполните на сервере из корня проверенного клона:

```sh
PUBLIC_IP=YOUR_PUBLIC_IPV4
sudo apt-get update
sudo apt-get install -y squid-openssl apache2-utils python3-venv nftables
sudo systemctl stop squid
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo python3 -m venv /opt/agy-certbot
sudo /opt/agy-certbot/bin/pip install 'certbot>=5.4,<6'
sudo /opt/agy-certbot/bin/certbot certonly --standalone --preferred-profile shortlived \
  --cert-name agy-proxy --ip-address "$PUBLIC_IP"
```

Certbot проверяет владение адресом через порт 80. IP-сертификат действует около шести дней и требует автоматического продления. Подробнее в [инструкции Let’s Encrypt для IP](https://letsencrypt.org/2026/03/11/shorter-certs-certbot).

## Правила и пользователи

[Конфигурация Squid](../deploy/squid.conf) принимает только аутентифицированный CONNECT к порту назначения 443. Она блокирует внутренние IPv4, адреса локального хоста, метаданные и служебные IPv6-адреса. Правило nft независимо блокирует TCP-соединения процесса прокси к внутренним IPv4 и собственному публичному IP, а также исходящий IPv6: смена DNS-ответа не открывает путь во внутреннюю сеть. На сервере с публично адресованными внутренними сетями добавьте их диапазоны в оба списка. TLS-соединение с конечным сайтом не перехватывается.

```sh
sudo cp -n /etc/squid/squid.conf /etc/squid/squid.conf.before-agy
sudo install -m 0644 deploy/squid.conf /etc/squid/squid.conf
sed "s/PUBLIC_IP/$PUBLIC_IP/g" deploy/guard.nft.in | sudo tee /etc/squid/agy-guard.nft >/dev/null
sudo nft -c -f /etc/squid/agy-guard.nft
sudo install -d /etc/systemd/system/squid.service.d /etc/letsencrypt/renewal-hooks/deploy
sudo install -m 0644 deploy/agy-guard.conf /etc/systemd/system/squid.service.d/
sudo install -m 0644 deploy/agy-certbot.service deploy/agy-certbot.timer /etc/systemd/system/
sudo install -m 0755 deploy/renew-proxy-cert /etc/letsencrypt/renewal-hooks/deploy/agy-proxy
sudo env RENEWED_LINEAGE=/etc/letsencrypt/live/agy-proxy /etc/letsencrypt/renewal-hooks/deploy/agy-proxy
sudo test -e /etc/squid/agy-users || sudo install -m 0640 -o root -g proxy /dev/null /etc/squid/agy-users
sudo htpasswd -B /etc/squid/agy-users alice
sudo htpasswd -B /etc/squid/agy-users bob
sudo chown root:proxy /etc/squid/agy-users
sudo chmod 0640 /etc/squid/agy-users
sudo squid -k parse
sudo systemctl daemon-reload
sudo systemctl enable --now squid agy-certbot.timer
sudo /opt/agy-certbot/bin/certbot renew --cert-name agy-proxy --dry-run
```

Задайте каждому свой случайный пароль из менеджера паролей. Передайте каждому через защищённый канал **только его** URL вида `https://alice:PERSONAL_PASSWORD@PUBLIC_IP:443`. В URL закодируйте зарезервированные символы пароля или используйте пароль из букв, цифр, `-` и `_`. Пароли не входят в репозиторий, клиентскую таблицу MCP или команды shell.

## Проверка и отзыв

С внешнего компьютера проверьте IP с каждой учётной записью по [инструкции клиента](proxy-client.md). Запрос без учётных данных должен получить 407 на CONNECT. С действующими данными обращение к внутренним адресам должно вернуть 403. Проверьте, что `systemctl is-active squid agy-certbot.timer` выводит `active` для обоих и что проверка продления прошла.

Чтобы закрыть доступ одному человеку, удалите только его запись:

```sh
sudo htpasswd -D /etc/squid/agy-users bob
sudo chown root:proxy /etc/squid/agy-users
sudo chmod 0640 /etc/squid/agy-users
```

Сервер кэширует успешную авторизацию до минуты. Уже открытые соединения могут жить дольше. Для немедленного отключения выполните `sudo systemctl restart squid`: это кратко оборвёт соединения всех клиентов, остальные смогут переподключиться со своими прежними паролями. Выключение сервиса: `sudo systemctl disable --now squid agy-certbot.timer`; после этого уберите **только добавленные для него** правила из сетевого файрвола и UFW и при необходимости таблицу `inet agy_proxy` из nftables.
