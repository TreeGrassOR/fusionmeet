"""Russian locale strings."""

from summary.core.locales.strings import LocaleStrings

STRINGS = LocaleStrings(
    empty_transcription="""
**В записи не обнаружено звука для расшифровки.**

*Если вы считаете, что это ошибка, напишите в нашу техническую
поддержку: visio@numerique.gouv.fr*

.

.

.

Что стоит проверить:
- Был ли включен микрофон?
- Находились ли вы достаточно близко к микрофону?
- Хороший ли у вас микрофон?
- Длится ли запись дольше 30 секунд?

""",
    download_header_template=(
        "\n*[Скачать запись (внешняя ссылка)]({download_link})*\n"
    ),
    form_footer_template=(
        "\n\n*[Оцените расшифровку (внешняя ссылка)]({form_link})*\n"
    ),
    hallucination_replacement_text="[Не удалось распознать речь]",
    summary_title_template="Краткое содержание: {title}",
)
