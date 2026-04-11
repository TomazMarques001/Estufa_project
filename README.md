<img width="1079" height="1048" alt="image" src="https://github.com/user-attachments/assets/1a2f9583-d572-40c9-8dea-da1ee4e9df2a" /><h1>Descrição e funcionalidades:</h1>

Esse projeto apresenta uma organização de containers focados em realizar comunicação com CLP (Controlador lógico Programavel) e disponibilizar os dados via MQQT Broker para aplicações customizaveis.<br><br>
O projeto eh divido em duas frentes:<br><br>
EDGE-INFRA: lida com a comunicação local com o CLP SIEMENS via ETHERNET, sendo necessario acessar o objeto CLP via o IP do mesmo, tendo em vista que os SIEMENS S7 1200+ tem um servidor OPC UA nativo.<br><br>
EDGE-APP: realiza a leitura dos tópicos no MQTT via Node red, que por sua vez comunica e realiza as regras de negocio entre o broker e as demais aplicações contidas no compose, de forma a centralizar regras de negocio, monitoramento, e aquisição de informações.<br><br>
O edge connector disponibiliza as informações captadas via protocolos OPC UA e MQTT, pela arquitetura padrão nesses casos foi usado o protocolo MQTT pra disponiblizar as informações via BROKER, e o OPC UA pra receber informações pertinentes a lógica do CLP.<br>
MQTT publica as informações em tópicos customizaveis no broker.<br>
OPC UA recebe informações atraves do valor e nodeid e insere dentro das funções do CLP.<br><br>
<p align="center">
  <img src="https://github.com/user-attachments/assets/807bab0b-0a08-4e80-a23b-2e0f6e88a722" width="70%" />
</p>
