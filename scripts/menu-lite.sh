#!/usr/bin/env bash
# Lite navigation; all operations remain in their existing domain helpers.
xm_home() {
    local choice
    while true; do
        xm_header 'Главная'
        printf '  Сервер: %s\n  Подписки: %b · локальный доступ\n\n' "$SERVER_IP" "$(get_sub_server_status)"
        printf '  [1] Snell v5: %b | v6: %s\n' "$(get_snell_status)" "$(xm_snell6_status)"
        printf '  [2] Mieru        %b\n  [3] MasterDNS    %b\n' "$(get_mieru_status)" "$(get_dns_status)"
        printf '  [4] qWDTT        %b\n  [5] CSQTT        %b\n' "$(get_wdtt_status)" "$(get_csqtt_status)"
        printf '  [6] Пользователи и подписки\n  [7] Watchdog\n  [8] Сеть и оптимизация\n  [9] Обновление и обслуживание\n  [0] Выход\n'
        read -r -p 'Раздел: ' choice || return
        case "$choice" in
            1) xm_snell_menu;; 2) menu_mieru;; 3) menu_dns;; 4) menu_wdtt;; 5) menu_csqtt;;
            6) menu_subscriptions;; 7) xm_watchdog_menu;; 8) xm_lite_network_menu;; 9) xm_lite_maintenance;;
            0) echo 'Меню закрыто. Службы продолжают работать.'; return;;
            *) echo 'Выберите номер из списка.';;
        esac
    done
}
xm_lite_network_menu() {
    local choice
    while true; do
        xm_header 'Сеть и оптимизация'
        printf '  [1] Xray: шлюзы и маршрутизация\n  [2] Адаптивный профиль BBR/fq, буферы, conntrack\n  [3] Дополнительные параметры TCP\n  [4] DNS системы\n  [5] Swap\n  [6] Fail2ban / защита SSH\n  [7] Доступ к портам служб\n  [8] Диагностика сети\n  [9] Сертификаты\n  [0] Назад\n'
        read -r -p 'Действие: ' choice || return
        case "$choice" in
            1) xm_xray_menu;; 2) xm_network_profile_menu;; 3) xm_tcp_menu;; 4) menu_dns_system;;
            5) menu_swap_manager;; 6) menu_fail2ban;; 7) menu_firewall;;
            8) python3 "$(dirname "${BASH_SOURCE[0]}")/network-diagnostics.py"; xm_pause;;
            9) menu_ssl;; 0) return;;
        esac
    done
}
xm_tcp_menu() {
    local choice n=no idle=no tfo=no helper
    local nlabel ilabel tlabel
    local -a args=()
    helper="$(dirname "${BASH_SOURCE[0]}")/tcp-profile.py"
    while true; do
        xm_header 'Дополнительные параметры TCP'
        nlabel=Выключено; ilabel=Выключено; tlabel=Выключено
        [ "$n" != yes ] || nlabel=Включено
        [ "$idle" != yes ] || ilabel=Включено
        [ "$tfo" != yes ] || tlabel=Включено
        printf '  [1] Непереданные TCP-данные: 16 KiB — %s\n' "$nlabel"
        printf '  [2] Сохранять окно после простоя — %s\n' "$ilabel"
        printf '  [3] Fast Open клиент/сервер — %s\n' "$tlabel"
        printf '  [4] Просмотр выбранных изменений\n  [5] Применить с резервной копией\n  [6] Откатить\n  [0] Назад\n'
        echo 'Это не лимит всех сетевых буферов. TFO требует поддержки приложения.'
        read -r -p 'Действие: ' choice || return
        args=(); [ "$n" != yes ] || args+=(--notsent-lowat)
        [ "$idle" != yes ] || args+=(--keep-cwnd)
        [ "$tfo" != yes ] || args+=(--fastopen)
        case "$choice" in
            1) if [ "$n" = no ]; then n=yes; else n=no; fi;;
            2) if [ "$idle" = no ]; then idle=yes; else idle=no; fi;;
            3) if [ "$tfo" = no ]; then tfo=yes; else tfo=no; fi;;
            4) python3 "$helper" plan "${args[@]}"; xm_pause;;
            5) python3 "$helper" plan "${args[@]}" && xm_confirm 'Применить показанные настройки TCP?' && python3 "$helper" apply "${args[@]}"; xm_pause;;
            6) xm_confirm 'Восстановить предыдущие значения TCP?' && python3 "$helper" rollback; xm_pause;;
            0) return;;
        esac
    done
}
xm_lite_maintenance() {
    local choice
    while true; do
        xm_header 'Обслуживание'
        echo '  Источник обновлений: lesovoi53/x-manager-lite'
        printf '  [1] Обновить Lite\n  [2] Создать резервную копию\n  [3] Восстановить резервную копию\n  [4] Пересканировать службы\n  [5] Перезапустить работающие службы Lite\n  [6] Запуск, остановка и автозапуск\n  [7] Ежедневная перезагрузка VPS\n  [8] Версии компонентов\n  [0] Назад\n'
        read -r -p 'Действие: ' choice || return
        case "$choice" in
            1) xm_release_update;;
            2) bash /usr/local/share/x-manager/distribution/install.sh --backup; xm_pause;;
            3) xm_backup_menu;; 4) rescan_and_adopt_protocols;;
            5) xm_confirm 'Перезапустить работающие службы Lite? Соединения будут прерваны.' && restart_all_services;;
            6) python3 "$(dirname "${BASH_SOURCE[0]}")/service-control.py" menu;;
            7) xm_reboot_menu;;
            8) python3 -c 'import json; d=json.load(open("/usr/local/share/x-manager/components.json")); print("Lite",d["release"]); [print(k+": "+v) for k,v in d["versions"].items()]'; xm_pause;;
            0) return;;
        esac
    done
}
