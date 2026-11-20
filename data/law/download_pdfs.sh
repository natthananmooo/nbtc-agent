#!/bin/bash
# โหลด PDF กฎหมาย กสทช. ที่เกี่ยวกับการร้องเรียนและการคุ้มครองผู้ใช้บริการ
# รันบน Terminal ของ Mac เท่านั้น เพราะ shell ที่ Claude ใช้ออกเน็ตไปโดเมนนี้ไม่ได้
set -u
cd "$(dirname "$0")/pdf" || exit 1
ok=0; fail=0

echo "[01/36] พระราชบัญญัติองค์กรจัดสรรคลื่นความถี่และกำกับการประกอบกิจการ"
curl -fsSL --max-time 90 -o "01_act.pdf" "https://broadcast.nbtc.go.th/data/document/law/doc/th/560400000025.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[02/36] พระราชบัญญัติองค์กรจัดสรรคลื่นความถี่และกำกับการประกอบกิจการ"
curl -fsSL --max-time 90 -o "02_act.pdf" "https://ratchakitcha.soc.go.th/documents/1861170.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[03/36] พระราชบัญญัติองค์กรจัดสรรคลื่นความถี่"
curl -fsSL --max-time 90 -o "03_act.pdf" "https://ratchakitcha.soc.go.th/documents/612606084368040776.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[04/36] พระราชบัญญัติการประกอบกิจการกระจายเสียงและกิจการโทรทัศน์ พ.ศ"
curl -fsSL --max-time 90 -o "04_act.pdf" "https://ratchakitcha.soc.go.th/documents/243263.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[05/36] พระราชบัญญัติจัดตั้งศาลปกครองและวิธีพิจารณาคดีปกครอง พ.ศ. 25"
curl -fsSL --max-time 90 -o "05_act.pdf" "https://www.dsi.go.th/Files/Images/img25610830153016-%E0%B8%9E%E0%B8%A3%E0%B8%B0%E0%B8%A3%E0%B8%B2%E0%B8%8A%E0%B8%9A%E0%B8%B1%E0%B8%8D%E0%B8%8D%E0%B8%B1%E0%B8%95%E0%B8%B4%E0%B8%88%E0%B8%B1%E0%B8%94%E0%B8%95%E0%B8%B1%E0%B9%89%E0%B8%87%E0%B8%A8%E0%B8%B2%E0%B8%A5%E0%B8%9B%E0%B8%81%E0%B8%84%E0%B8%A3%E0%B8%AD%E0%B8%87%E0%B9%81%E0%B8%A5%E0%B8%B0%E0%B8%A7%E0%B8%B4%E0%B8%98%E0%B8%B5%E0%B8%9E%E0%B8%B4%E0%B8%88%E0%B8%B2%E0%B8%A3%E0%B8%93%E0%B8%B2%E0%B8%84%E0%B8%94%E0%B8%B5%E0%B8%9B%E0%B8%81%E0%B8%84%E0%B8%A3%E0%B8%AD%E0%B8%872542.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[06/36] พระราชบัญญัติจัดตั้งศาลปกครองและวิธีพิจารณาคดีปกครอง พ.ศ. 25"
curl -fsSL --max-time 90 -o "06_act.pdf" "https://ratchakitcha.soc.go.th/documents/1708982.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[07/36] พระราชบัญญัติวิธีปฏิบัติราชการทางปกครอง พ.ศ. 2539 และที่แก้ไ"
curl -fsSL --max-time 90 -o "07_act.pdf" "https://law.prd.go.th/th/content/page/index/id/234543" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[08/36] พระราชบัญญัติวิธีปฏิบัติราชการทางปกครอง พ.ศ. 2539"
curl -fsSL --max-time 90 -o "08_act.pdf" "https://ratchakitcha.soc.go.th/documents/1670595.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[09/36] พระราชบัญญัติการอำนวยความสะดวกในการพิจารณาอนุญาตของทางราชการ"
curl -fsSL --max-time 90 -o "09_act.pdf" "https://ratchakitcha.soc.go.th/documents/2031959.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[10/36] เรื่อง หลักเกณฑ์และวิธีการพักหรือหยุดการให้บริการโทรคมนาคม"
curl -fsSL --max-time 90 -o "10_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/17114598.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[11/36] เรื่อง หลักเกณฑ์บริการคงสิทธิเลขหมายโทรศัพท์เคลื่อนที่"
curl -fsSL --max-time 90 -o "11_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/17182391.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[12/36] เรื่อง การกำหนดและกำกับดูแลโครงสร้างอัตราค่าบริการโทรศัพท์เค"
curl -fsSL --max-time 90 -o "12_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/17111910.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[13/36] เรื่อง การกำหนดและกำกับดูแลอัตราขั้นสูงของค่าบริการโทรศัพท์เ"
curl -fsSL --max-time 90 -o "13_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/17152352.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[14/36] เรื่อง หลักเกณฑ์และวิธีการเกี่ยวกับการใช้สิทธิในการปักหรือตั"
curl -fsSL --max-time 90 -o "14_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/2099191.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[15/36] เรื่อง การลดหรือยกเว้นค่าบริการสำหรับการให้บริการข้อมูลข่าวส"
curl -fsSL --max-time 90 -o "15_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/51169.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[16/36] เรื่อง อัตราขั้นสูงของค่าบริการและการเรียกเก็บเงินค่าบริการล"
curl -fsSL --max-time 90 -o "16_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/201024.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[17/36] เรื่อง อัตราขั้นสูงของค่าบริการโทรคมนาคมสำหรับบริการโทรศัพท์"
curl -fsSL --max-time 90 -o "17_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/1920303.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[18/36] เรื่อง ยกเลิกอัตราค่าบริการขั้นสูงชั่วคราว ตามประกาศคณะกรรมก"
curl -fsSL --max-time 90 -o "18_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/1944035.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[19/36] เรื่อง การกำหนดวงเงินคงเหลือสะสมสูงสุดในการเรียกเก็บเงินค่าบ"
curl -fsSL --max-time 90 -o "19_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/2129482.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[20/36] เรื่อง หลักเกณฑ์การเรียกเก็บค่าบริการล่วงหน้าของบริการอินเทอ"
curl -fsSL --max-time 90 -o "20_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/17189694.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[21/36] เรื่อง หลักเกณฑ์การเรียกเก็บค่าบริการล่วงหน้าในกิจการโทรคมนา"
curl -fsSL --max-time 90 -o "21_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/140D232S0000000001200.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[22/36] เรื่อง อัตราขั้นสูงของค่าบริการและการเรียกเก็บเงินค่าบริการล"
curl -fsSL --max-time 90 -o "22_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/21473.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[23/36] เรื่อง หลักเกณฑ์และวิธีการนำค่าใช้จ่ายที่เกิดขึ้นจากภารกิจด้"
curl -fsSL --max-time 90 -o "23_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/17063538.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[24/36] เรื่อง หลักเกณฑ์และวิธีการจัดเก็บรายได้เพื่อนำไปใช้ในการจัดใ"
curl -fsSL --max-time 90 -o "24_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/1992923.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[25/36] เรื่อง มาตรฐานของคุณภาพการให้บริการโทรคมนาคม (ฉบับที่ 2)"
curl -fsSL --max-time 90 -o "25_protect.pdf" "https://ratchakitcha.soc.go.th/documents/49556.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[26/36] เรื่อง มาตรฐานของคุณภาพการให้บริการโทรคมนาคม"
curl -fsSL --max-time 90 -o "26_protect.pdf" "https://ratchakitcha.soc.go.th/documents/17109195.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[27/36] เรื่อง มาตรการคุ้มครองสิทธิของผู้ใช้บริการโทรคมนาคมเกี่ยวกับ"
curl -fsSL --max-time 90 -o "27_protect.pdf" "https://ratchakitcha.soc.go.th/documents/140D215S0000000002700.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[28/36] เรื่อง มาตรการคุ้มครองผู้ใช้บริการเป็นการชั่วคราวในกรณีสิ้นส"
curl -fsSL --max-time 90 -o "28_protect.pdf" "https://ratchakitcha.soc.go.th/documents/1982316.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[29/36] เรื่อง มาตรการคุ้มครองผู้ใช้บริการเป็นการชั่วคราวในกรณีสิ้นส"
curl -fsSL --max-time 90 -o "29_protect.pdf" "https://ratchakitcha.soc.go.th/documents/2052893.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[30/36] เรื่อง มาตรฐานของสัญญาการให้บริการโทรคมนาคม พ.ศ. 2549"
curl -fsSL --max-time 90 -o "30_protect.pdf" "https://www.ratchakitcha.soc.go.th/DATA/PDF/2549/E/099/7.PDF" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[31/36] เรื่อง การขอความเห็นชอบสัญญาอันเกี่ยวเนื่องกับการประกอบกิจกา"
curl -fsSL --max-time 90 -o "31_protect.pdf" "https://ratchakitcha.soc.go.th/documents/201023.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[32/36] เรื่อง กระบวนการรับและพิจารณาเรื่องร้องเรียนเกี่ยวกับการประก"
curl -fsSL --max-time 90 -o "32_protect.pdf" "https://ratchakitcha.soc.go.th/documents/2079698.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[33/36] เรื่อง การกระทําที่น่าจะเป็นการเอาเปรียบผู้บริโภคในกิจการโทร"
curl -fsSL --max-time 90 -o "33_protect.pdf" "https://ratchakitcha.soc.go.th/documents/2051700.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[34/36] เรื่อง แผนการจัดให้มีบริการโทรคมนาคมพื้นฐานโดยทั่วถึงและบริก"
curl -fsSL --max-time 90 -o "34_protect.pdf" "https://ratchakitcha.soc.go.th/documents/31696.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[35/36] เรื่อง หลักเกณฑ์และวิธีการงดหรือระงับการให้บริการโทรศัพท์เคล"
curl -fsSL --max-time 90 -o "35_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/1801846.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }
echo "[36/36] เรื่อง กำหนดระยะเวลาการพิจารณาคำขอหรือคำร้องเรียนในกิจการโทร"
curl -fsSL --max-time 90 -o "36_keyword.pdf" "https://ratchakitcha.soc.go.th/documents/15882.pdf" && ok=$((ok+1)) || { echo "   โหลดไม่ได้"; fail=$((fail+1)); }

echo
echo "สำเร็จ $ok ฉบับ  ล้มเหลว $fail ฉบับ"
ls -la *.pdf | wc -l
