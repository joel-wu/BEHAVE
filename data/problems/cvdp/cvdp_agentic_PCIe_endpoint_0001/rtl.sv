module PCIe_endpoint #(
    parameter int ADDR_WIDTH=64,
    parameter int DATA_WIDTH=128
)(
    input logic clk_level, reset_n,
    input logic [DATA_WIDTH-1:0] pcie_rx_tlp,
    input logic pcie_rx_valid, pcie_tx_ready,
    output wire pcie_rx_ready,
    output logic [DATA_WIDTH-1:0] pcie_tx_tlp,
    output logic pcie_tx_valid,
    input logic dma_request,
    input logic [ADDR_WIDTH-1:0] dma_src, dma_dst,
    input logic [7:0] dma_words,
    output wire dma_ready,
    output logic dma_complete, dma_error, msix_interrupt,
    output wire mem_valid, mem_rsp_ready,
    output logic mem_write,
    output logic [ADDR_WIDTH-1:0] mem_addr,
    output logic [DATA_WIDTH-1:0] mem_wdata,
    input logic mem_ready, mem_rsp_valid, mem_error,
    input logic [DATA_WIDTH-1:0] mem_rdata
);
    localparam int P=DATA_WIDTH-ADDR_WIDTH-8;
    localparam logic [2:0] IDLE=0, HOST_REQ=1, HOST_RSP=2, READ_REQ=3,
        READ_RSP=4, WRITE_REQ=5, WRITE_RSP=6;
    logic [2:0] phase=IDLE;
    logic previous_dma=0;
    logic [ADDR_WIDTH-1:0] host_addr=0, source=0, destination=0;
    logic [P-1:0] host_payload=0;
    logic [7:0] remaining=0;
    wire [7:0] opcode=pcie_rx_tlp[DATA_WIDTH-1 -: 8];
    wire [ADDR_WIDTH-1:0] packet_addr=pcie_rx_tlp[P +: ADDR_WIDTH];
    wire [P-1:0] payload=pcie_rx_tlp[P-1:0];
    assign dma_ready=reset_n && phase==IDLE && !pcie_tx_valid;
    assign pcie_rx_ready=dma_ready && !dma_request;
    assign mem_valid=reset_n && (phase==HOST_REQ || phase==READ_REQ || phase==WRITE_REQ);
    assign mem_rsp_ready=reset_n && (phase==HOST_RSP || phase==READ_RSP || phase==WRITE_RSP);
    initial begin
        pcie_tx_tlp=0; pcie_tx_valid=0; dma_complete=0; dma_error=0; msix_interrupt=0;
        mem_write=0; mem_addr=0; mem_wdata=0;
    end
    always_ff @(posedge clk_level or negedge reset_n) begin
        if(!reset_n) begin
            phase<=IDLE; previous_dma<=0; host_addr<=0; host_payload<=0;
            source<=0; destination<=0; remaining<=0;
            pcie_tx_tlp<=0; pcie_tx_valid<=0; dma_complete<=0; dma_error<=0; msix_interrupt<=0;
            mem_write<=0; mem_addr<=0; mem_wdata<=0;
        end else begin
            previous_dma<=dma_request;
            dma_complete<=0; dma_error<=0; msix_interrupt<=0;
            case(phase)
                IDLE: begin
                    if(pcie_tx_valid) begin
                        if(pcie_tx_ready) pcie_tx_valid<=0;
                    end else if(dma_request) begin
                        if(!previous_dma) begin
                            source<=dma_src; destination<=dma_dst; remaining<=dma_words;
                            if(dma_words==0) begin dma_complete<=1; msix_interrupt<=1; end
                            else begin mem_addr<=dma_src; mem_wdata<=0; mem_write<=0; phase<=READ_REQ; end
                        end
                    end else if(pcie_rx_valid) begin
                        host_addr<=packet_addr; host_payload<=payload;
                        if(opcode==8'h01 || opcode==8'h02) begin
                            mem_addr<=packet_addr; mem_wdata<=opcode==8'h01 ? DATA_WIDTH'(payload) : '0;
                            mem_write<=opcode==8'h01; phase<=HOST_REQ;
                        end else begin pcie_tx_tlp<={8'hff,packet_addr,{P{1'b0}}}; pcie_tx_valid<=1; end
                    end
                end
                HOST_REQ: if(mem_ready) phase<=HOST_RSP;
                HOST_RSP: if(mem_rsp_valid) begin
                    if(mem_error) pcie_tx_tlp<={8'hfe,host_addr,{P{1'b0}}};
                    else if(mem_write) pcie_tx_tlp<={8'h81,host_addr,host_payload};
                    else pcie_tx_tlp<={8'h82,host_addr,mem_rdata[P-1:0]};
                    pcie_tx_valid<=1; phase<=IDLE;
                end
                READ_REQ: if(mem_ready) phase<=READ_RSP;
                READ_RSP: if(mem_rsp_valid) begin
                    if(mem_error) begin phase<=IDLE; dma_complete<=1; dma_error<=1; end
                    else begin mem_addr<=destination; mem_wdata<=mem_rdata; mem_write<=1; phase<=WRITE_REQ; end
                end
                WRITE_REQ: if(mem_ready) phase<=WRITE_RSP;
                WRITE_RSP: if(mem_rsp_valid) begin
                    if(mem_error || remaining==1) begin
                        phase<=IDLE; remaining<=0; dma_complete<=1;
                        dma_error<=mem_error; msix_interrupt<=!mem_error;
                    end else begin
                        remaining<=remaining-1'b1; source<=source+1'b1; destination<=destination+1'b1;
                        mem_addr<=source+1'b1; mem_wdata<=0; mem_write<=0; phase<=READ_REQ;
                    end
                end
                default: phase<=IDLE;
            endcase
        end
    end
endmodule
