module dma_xfer_engine #(
    parameter int DMA_B=0,
    parameter int DMA_HW=1,
    parameter int DMA_W=2
) (
    input logic clk, rstn,
    input logic [3:0] addr,
    input logic we,
    input logic [31:0] wd,
    input logic dma_req, bus_grant,
    input logic [31:0] rd_m,
    output logic [31:0] rd,
    output logic bus_req, bus_lock,
    output logic [31:0] addr_m,
    output logic we_m,
    output logic [31:0] wd_m,
    output logic [1:0] size_m
);
    logic [9:0] control_q;
    logic [31:0] source_q, destination_q, buffer_q;
    logic [3:0] remaining;
    logic [1:0] phase; // 0 idle, 1 read/request, 2 write
    function automatic [2:0] bytes_for(input [1:0] code);
        if(code==DMA_B) bytes_for=1;
        else if(code==DMA_HW) bytes_for=2;
        else if(code==DMA_W) bytes_for=4;
        else bytes_for=0;
    endfunction
    function automatic [31:0] mask_for(input [2:0] size);
        case(size)
            1: mask_for=32'hff;
            2: mask_for=32'hffff;
            4: mask_for=32'hffffffff;
            default: mask_for=0;
        endcase
    endfunction
    wire [2:0] source_bytes=bytes_for(control_q[5:4]);
    wire [2:0] destination_bytes=bytes_for(control_q[7:6]);
    always_comb begin
        rd=0;
        if(!we) case(addr)
            0: rd={22'b0,control_q};
            4: rd=source_q;
            8: rd=destination_q;
            default: rd=0;
        endcase
        bus_req=(phase!=0); bus_lock=(phase!=0);
        addr_m=0; we_m=0; wd_m=0; size_m=0;
        if(phase==1) begin addr_m=source_q; size_m=control_q[5:4]; end
        if(phase==2) begin
            addr_m=destination_q; we_m=1; size_m=control_q[7:6];
            wd_m=(buffer_q & mask_for(destination_bytes)) << (8*destination_q[1:0]);
        end
    end
    always_ff @(posedge clk or negedge rstn) begin
        if(!rstn) begin
            control_q<=0; source_q<=0; destination_q<=0; buffer_q<=0;
            remaining<=0; phase<=0;
        end else if(phase==0) begin
            if(we) case(addr)
                0: control_q<=wd[9:0];
                4: source_q<=wd;
                8: destination_q<=wd;
                default: begin end
            endcase
            else if(dma_req && control_q[3:0]!=0 && source_bytes!=0 && destination_bytes!=0 &&
                    (source_q & (32'(source_bytes)-1))==0 &&
                    (destination_q & (32'(destination_bytes)-1))==0) begin
                remaining<=control_q[3:0]; phase<=1;
            end
        end else if(bus_grant) begin
            if(phase==1) begin
                buffer_q<=(rd_m >> (8*source_q[1:0])) & mask_for(source_bytes);
                if(control_q[8]) source_q<=source_q+32'(source_bytes);
                phase<=2;
            end else begin
                if(control_q[9]) destination_q<=destination_q+32'(destination_bytes);
                remaining<=remaining-1'b1;
                phase<=(remaining==1) ? 0 : 1;
            end
        end
    end
endmodule
