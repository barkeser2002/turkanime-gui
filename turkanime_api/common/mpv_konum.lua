-- TürkAnime: oynatma bittiğinde kaldığı yeri bir JSON dosyasına yazar.
--
-- Neden: mpv'nin kendi --save-position-on-quit'i konumu oynatılan ADRESE
-- bağlıyor. Kaynakların adresleri her istekte değişiyor (?token=, ?md5=) ya
-- da best_video başka bir aday seçiyor; konum her seferinde kayboluyordu.
-- Uygulama konumu bölümün kendi anahtarıyla saklıyor ve bir sonraki
-- oynatmada --start ile veriyor (bkz. common/mpv_oynatici.py).
--
-- Rapor: {"konum": saniye, "sure": saniye, "sebep": "eof"|"quit"|"stop"|"error"}
-- "sebep" uygulamanın "bölüm bitti mi?" sorusunun cevabı: eof ise izlendi
-- sayılır ve ilerleme diyalog açılmadan yazılır.
local utils = require 'mp.utils'

local hedef = mp.get_opt('turkanime-konum')
if not hedef or hedef == '' then
    return
end

-- Dosya sonunda time-pos nil'e döner; son geçerli değer saklanır.
local konum, sure = nil, nil
mp.observe_property('time-pos', 'number', function(_, deger)
    if deger then konum = deger end
end)
mp.observe_property('duration', 'number', function(_, deger)
    if deger then sure = deger end
end)

mp.register_event('end-file', function(olay)
    local dosya = io.open(hedef, 'w')
    if not dosya then
        return
    end
    dosya:write(utils.format_json({konum = konum, sure = sure,
                                   sebep = olay.reason or ''}))
    dosya:close()
end)
