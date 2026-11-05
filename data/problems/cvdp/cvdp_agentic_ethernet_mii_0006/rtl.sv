module ethernet_mac_tx (
    input logic clk_in, rst_in, cfg_wr_in,
    input logic [31:0] cfg_addr_in, cfg_data_wr_in,
    input logic axis_tready_in,
    output logic [31:0] cfg_data_rd_out,
    output logic interrupt_out, axis_tvalid_out,
    output logic [31:0] axis_tdata_out,
    output logic [3:0] axis_tstrb_out,
    output logic axis_tlast_out
);
    logic [31:0] memory [0:1023];
    logic [31:0] cfg_read_q;
    logic [15:0] length_q;
    logic irq_enable_q, program_q;
    logic [1:0] phase_q;
    logic [10:0] offset_q;
    wire [15:0] csr=cfg_addr_in[15:0];
    wire [15:0] total_length=(length_q<64) ? 16'd64 : length_q;
    always_comb begin
        cfg_data_rd_out=0;
        if(!cfg_wr_in) begin
            case(csr)
                16'h07f4: cfg_data_rd_out={16'd0,length_q};
                16'h07f8: cfg_data_rd_out={31'd0,irq_enable_q};
                16'h07fc: cfg_data_rd_out={30'd0,program_q,(phase_q!=0)};
                default: cfg_data_rd_out=cfg_read_q;
            endcase
        end
    end
    always_ff @(posedge clk_in or posedge rst_in) begin
        if(rst_in) begin
            for(integer i=0;i<1024;i++) memory[i]<=0;
            cfg_read_q<=0; length_q<=0; irq_enable_q<=0; program_q<=0;
            phase_q<=0; offset_q<=0;
            interrupt_out<=0; axis_tvalid_out<=0; axis_tdata_out<=0;
            axis_tstrb_out<=0; axis_tlast_out<=0;
        end else begin
            cfg_read_q<=memory[cfg_addr_in[11:2]];
            interrupt_out<=0;
            case(phase_q)
                0: if(cfg_wr_in) begin
                    case(csr)
                        16'h07f4: length_q<=(cfg_data_wr_in[15:0]>1518) ? 16'd1518 : cfg_data_wr_in[15:0];
                        16'h07f8: irq_enable_q<=cfg_data_wr_in[0];
                        16'h07fc: begin
                            program_q<=cfg_data_wr_in[1];
                            if(cfg_data_wr_in[0]) begin
                                phase_q<=cfg_data_wr_in[1] ? 2'd3 : 2'd1;
                                offset_q<=0;
                            end
                        end
                        default: if(cfg_addr_in<32'h1000) memory[cfg_addr_in[11:2]]<=cfg_data_wr_in;
                    endcase
                end
                1: begin
                    axis_tvalid_out<=1;
                    for(integer lane=0;lane<4;lane++) begin
                        axis_tdata_out[8*lane +: 8]<=((int'(offset_q)+lane)<int'(length_q)) ? memory[offset_q[10:2]][8*lane +: 8] : 8'd0;
                        axis_tstrb_out[lane]<=((int'(offset_q)+lane)<int'(total_length));
                    end
                    axis_tlast_out<=(16'(offset_q)+16'd4>=total_length);
                    phase_q<=2;
                end
                2: if(axis_tready_in) begin
                    axis_tvalid_out<=0; axis_tdata_out<=0; axis_tstrb_out<=0; axis_tlast_out<=0;
                    if(axis_tlast_out) phase_q<=3;
                    else begin offset_q<=offset_q+11'd4; phase_q<=1; end
                end
                3: begin phase_q<=0; program_q<=0; interrupt_out<=irq_enable_q; end
            endcase
        end
    end
endmodule
