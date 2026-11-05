module precision_counter_axi #(
    parameter int C_S_AXI_DATA_WIDTH=32,
    parameter int C_S_AXI_ADDR_WIDTH=8
) (
    input logic axi_aclk, axi_aresetn,
    input logic [C_S_AXI_ADDR_WIDTH-1:0] axi_awaddr,
    input logic axi_awvalid,
    input logic [C_S_AXI_DATA_WIDTH-1:0] axi_wdata,
    input logic [C_S_AXI_DATA_WIDTH/8-1:0] axi_wstrb,
    input logic axi_wvalid, axi_bready,
    input logic [C_S_AXI_ADDR_WIDTH-1:0] axi_araddr,
    input logic axi_arvalid, axi_rready,
    output logic axi_awready, axi_wready,
    output logic [1:0] axi_bresp,
    output logic axi_bvalid, axi_arready,
    output logic [C_S_AXI_DATA_WIDTH-1:0] axi_rdata,
    output logic [1:0] axi_rresp,
    output logic axi_rvalid, axi_ap_done, irq
);
    logic [31:0] ctl_q, elapsed_q, count_q, mask_q, threshold_q;
    logic aw_full, w_full;
    logic [C_S_AXI_ADDR_WIDTH-1:0] address_q;
    logic [C_S_AXI_DATA_WIDTH-1:0] data_q;
    logic [C_S_AXI_DATA_WIDTH/8-1:0] strobe_q;
    logic [31:0] next_elapsed, next_count;
    assign axi_awready=!aw_full && !axi_bvalid;
    assign axi_wready=!w_full && !axi_bvalid;
    assign axi_arready=!axi_rvalid;
    assign axi_ap_done=(count_q==0);
    assign irq=ctl_q[0] && mask_q[0] && count_q==threshold_q;
    function automatic [31:0] merge_bytes(input [31:0] old_value,
        input [C_S_AXI_DATA_WIDTH-1:0] new_value,input [C_S_AXI_DATA_WIDTH/8-1:0] strobes);
        reg [31:0] value;
        begin
            value=old_value;
            for(integer j=0;j<4 && j<C_S_AXI_DATA_WIDTH/8;j++)
                if(strobes[j]) value[j*8 +: 8]=new_value[j*8 +: 8];
            merge_bytes=value;
        end
    endfunction
    always_comb begin
        next_elapsed=elapsed_q; next_count=count_q;
        if(ctl_q[0]) begin
            if(count_q!=0) next_count=count_q-1'b1;
            else next_elapsed=elapsed_q+1'b1;
        end
    end
    always_ff @(posedge axi_aclk or negedge axi_aresetn) begin
        if(!axi_aresetn) begin
            ctl_q<=0; elapsed_q<=0; count_q<=0; mask_q<=0; threshold_q<=0;
            aw_full<=0; w_full<=0; address_q<=0; data_q<=0; strobe_q<=0;
            axi_bvalid<=0; axi_bresp<=0; axi_rvalid<=0; axi_rdata<=0; axi_rresp<=0;
        end else begin
            elapsed_q<=next_elapsed; count_q<=next_count;
            if(axi_arready && axi_arvalid) begin
                axi_rvalid<=1; axi_rresp<=0;
                case(axi_araddr)
                    0: axi_rdata<=C_S_AXI_DATA_WIDTH'(ctl_q);
                    12: axi_rdata<=C_S_AXI_DATA_WIDTH'(count_q==0);
                    16: axi_rdata<=C_S_AXI_DATA_WIDTH'(elapsed_q);
                    32: axi_rdata<=C_S_AXI_DATA_WIDTH'(count_q);
                    36: axi_rdata<=C_S_AXI_DATA_WIDTH'(mask_q);
                    40: axi_rdata<=C_S_AXI_DATA_WIDTH'(threshold_q);
                    default: begin axi_rdata<=0; axi_rresp<=2; end
                endcase
            end else if(axi_rvalid && axi_rready) begin axi_rvalid<=0; axi_rdata<=0; axi_rresp<=0; end
            if(axi_bvalid && axi_bready) begin axi_bvalid<=0; axi_bresp<=0; end
            if(aw_full && w_full && !axi_bvalid) begin
                aw_full<=0; w_full<=0; axi_bvalid<=1; axi_bresp<=0;
                case(address_q)
                    0: begin
                        ctl_q<=merge_bytes(ctl_q,data_q,strobe_q);
                        if((strobe_q & (C_S_AXI_DATA_WIDTH/8)'(15))!=0) elapsed_q<=0;
                    end
                    16: elapsed_q<=merge_bytes(next_elapsed,data_q,strobe_q);
                    32: count_q<=merge_bytes(next_count,data_q,strobe_q);
                    36: mask_q<=merge_bytes(mask_q,data_q,strobe_q);
                    40: threshold_q<=merge_bytes(threshold_q,data_q,strobe_q);
                    default: axi_bresp<=2;
                endcase
            end else begin
                if(axi_awready && axi_awvalid) begin aw_full<=1; address_q<=axi_awaddr; end
                if(axi_wready && axi_wvalid) begin w_full<=1; data_q<=axi_wdata; strobe_q<=axi_wstrb; end
            end
        end
    end
endmodule
