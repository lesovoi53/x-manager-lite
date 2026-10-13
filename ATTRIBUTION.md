# Происхождение и лицензии

X-Manager Lite основан на X-Manager `d97a3c50f51f729838376801d4e8fb9bb5edbea4`.
Уведомление об авторстве и MIT-лицензия исходного проекта сохранены в [LICENSE](LICENSE).
Лицензия менеджера не заменяет лицензии используемых сторонних программ.

| Компонент | Закреплённый источник | Материалы лицензии/исходников |
|---|---|---|
| Snell v5 | [Snell 5.0.1](https://dl.nssurge.com/snell/snell-server-v5.0.1-linux-amd64.zip) | Неизменённый закрытый бинарник поставщика; MIT менеджера на него не распространяется |
| Mieru / mita | [enfein/mieru v3.38.0](https://github.com/enfein/mieru/tree/v3.38.0) | [mieru.txt](licenses/mieru.txt), [mita.txt](licenses/mita.txt), GPL-3.0; полный `mita-source.tar.gz` в Release |
| Snell v6 | [sing-box-extended 1.14.1-extended-2.7.2](https://github.com/shtorm-7/sing-box-extended/releases/tag/v1.14.1-extended-2.7.2), commit `55faa763f986f4ca8a492d9b2719bc6330d2bef5` | [snell6.txt](licenses/snell6.txt); соответствующий `snell6-source.tar.gz` в Release |
| MasterDnsVPN | [masterking32/MasterDnsVPN v2026.06.13.234407-7de2476](https://github.com/masterking32/MasterDnsVPN/tree/v2026.06.13.234407-7de2476) | MIT, Amin Mahmoudi; [masterdns.txt](licenses/masterdns.txt) |
| TUNA Subscription Server | Встроенный модуль, унаследованный из X-Manager | [MIT](LICENSE) |

Lite не реализует заново протоколы, криптографию либо ядра этих проектов.
Его работа — установка фиксированного набора, конфигурация, управление службами,
маршрутизация, подписки, резервирование и пользовательский интерфейс.

Внешние программы, которыми Lite умеет управлять, но не распространяет:

- [Xray-core](https://github.com/XTLS/Xray-core): самостоятельное ядро маршрутизации;
- qWDTT: [SpaceNeuroX/proxy-turn-vk-android](https://github.com/SpaceNeuroX/proxy-turn-vk-android);
- CSQTT: [amurcanov/csqtt](https://github.com/amurcanov/csqtt), собственные условия
  PolyForm Noncommercial 1.0.0; не следует обозначать этот компонент как MIT;
- совместимая локальная установка X-UI: необязательная интеграция; панель не ставится.

Архивы исходников в Release сохраняют исходные лицензии, инструкции сборки,
go.mod/go.sum и зависимости. Бинарники этого выпуска перенесены без пересборки
из закреплённого набора X-Manager; контрольные суммы указаны в `components.json`.
Следующий выпуск обязан согласованно обновлять бинарник, исходники и хеши.
Оставленные в дереве дополнительные уведомления относятся к унаследованным
совместимым форматам полного проекта, а не к установленным демонам Lite.
